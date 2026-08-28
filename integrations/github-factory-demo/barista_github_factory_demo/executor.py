"""Compile and execute one GitHub issue as an ephemeral Factory App Run."""

from __future__ import annotations

import base64
import hashlib
import os
import re
import time
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlparse

from barista_app_sdk import (
    AppRun,
    BaristaClient,
    Config,
    GitHubForge,
    PatchArtifact,
    ResolvedGitRepository,
    deliver_draft_change,
    resolve_installed_app,
)
from barista_app_sdk.content import canonical_bytes, content_id
from barista_app_sdk.errors import ResultIntegrityError, TerminalError
from barista_app_sdk.sensitive import assert_no_high_confidence_secrets

from .config import ControllerConfig, worker_command_from_env
from .store import Claim

ACCEPTANCE_SCRIPT = """from pathlib import Path
import sys

number = int(sys.argv[1])
uri = sys.argv[2]
path = Path("issues") / f"issue-{number}.md"
text = path.read_text(encoding="utf-8")
assert text.startswith(f"# Issue {number}: ")
assert f"Source: {uri}\\n" in text
assert "\\n## Objective\\n\\n" in text
"""


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{time.time_ns()}")
    try:
        with temporary.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def build_factory_run(config: ControllerConfig, claim: Claim) -> AppRun:
    repository_hash = hashlib.sha256(config.repository.encode()).hexdigest()[:10]
    expected = (
        f"github-{repository_hash}-issue-{claim.issue_number}-attempt-{claim.attempt}"
    )
    legacy = f"github-{repository_hash}-issue-{claim.issue_number}"
    if claim.run_name != expected and not (
        claim.attempt == 1 and claim.run_name == legacy
    ):
        raise ValueError("claim run identity does not match repository issue attempt")
    run_name = claim.run_name
    branch = f"barista/issue-{claim.issue_number}"
    document = {
        "schema_version": "v1alpha1",
        "name": run_name,
        "app": config.factory_app,
        "operation": "software-change",
        "input": {
            "media_type": "application/json",
            "value": {
                "worker_app": config.worker_app,
                "tasks": [{"id": "issue", "command": worker_command_from_env()}],
                "acceptance": {
                    "command": [
                        "python",
                        ".barista/accept_issue.py",
                        str(claim.issue_number),
                        claim.issue_uri,
                    ],
                    "files": {".barista/accept_issue.py": ACCEPTANCE_SCRIPT},
                },
                "concurrency": 1,
                "timeout_seconds": 600,
                "patch_max_bytes": config.max_patch_bytes,
                "branch": branch,
                "commit_message": f"Record GitHub issue #{claim.issue_number}",
                "title": f"Record issue #{claim.issue_number}",
                "body": (
                    f"Automated draft for {claim.issue_uri}.\n\n"
                    "Factory integrated the isolated worker patch and ran the repository-owned acceptance check."
                ),
            },
        },
        "bindings": {
            "workspace": {
                "kind": "sh.barista.git.repository",
                "uri": config.repository,
                "ref": config.base_ref,
            },
            "objective": {"kind": "com.github.issue", "uri": claim.issue_uri},
        },
        "deliveries": {
            "change": {
                "kind": "com.github.draft-pull-request",
                "target": config.repository,
                "options": {
                    "base_ref": config.base_ref,
                    "head_branch": branch,
                    "executor": "runner",
                },
            }
        },
        "metadata": {
            "sh.barista.github-webhook": {
                "issue_number": claim.issue_number,
                "attempt": claim.attempt,
                **(
                    {
                        "answer_comment_id": claim.answer_comment_id,
                        "prior_result_digest": claim.prior_result_digest,
                    }
                    if claim.answer_comment_id is not None
                    else {}
                ),
            }
        },
    }
    return AppRun.parse(document)


def _capture(
    client, session_id: str, command: list[str], *, timeout: float = 120.0
) -> bytes:
    handle = client.exec(session_id, command, timeout_seconds=int(timeout))
    operation = client.wait_operation(handle.operation_id, timeout=timeout)
    if int((operation.result or {}).get("exit_code", 1)) != 0:
        raise ResultIntegrityError(
            "could not read Factory output",
            code="github_demo.output_unreadable",
        )
    output = bytearray()
    saw_exit = False
    for event in client.events(session_id, cursor=handle.event_cursor, max_events=100):
        if event.operation_id is not None and event.operation_id != handle.operation_id:
            continue
        if event.type == "exec.stdout":
            output.extend(base64.b64decode(event.data.get("chunk", ""), validate=True))
        elif event.type == "exec.exit":
            saw_exit = True
            break
    if not saw_exit:
        raise ResultIntegrityError(
            "Factory output event stream ended early",
            code="github_demo.output_incomplete",
        )
    return bytes(output)


def read_verified_patch(
    client,
    session_id: str,
    *,
    uri: str,
    expected_digest: str,
    expected_size: int,
    max_bytes: int,
    expected_run: str,
) -> PatchArtifact:
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc:
        raise ResultIntegrityError(
            "Factory patch URI is not a local file", code="github_demo.patch_uri"
        )
    path = unquote(parsed.path)
    pure_path = PurePosixPath(path)
    expected_prefix = PurePosixPath("/work/app-runs") / expected_run
    if (
        ".." in pure_path.parts
        or pure_path.parent != expected_prefix
        or "'" in path
        or "\n" in path
        or "\r" in path
    ):
        raise ResultIntegrityError(
            "Factory patch path is outside its run workspace",
            code="github_demo.patch_uri",
        )
    metadata = _capture(
        client,
        session_id,
        ["sh", "-c", f"set -e; wc -c < '{path}'; sha256sum '{path}'"],
    )
    try:
        lines = metadata.decode("ascii").splitlines()
        actual_size = int(lines[0].strip())
        announced_hash = lines[1].split()[0]
    except (UnicodeDecodeError, ValueError, IndexError) as exc:
        raise ResultIntegrityError(
            "Factory patch metadata is invalid", code="github_demo.patch_metadata"
        ) from exc
    if (
        actual_size != expected_size
        or actual_size < 0
        or actual_size > max_bytes
        or expected_digest != "sha256:" + announced_hash
    ):
        raise ResultIntegrityError(
            "Factory patch metadata does not match its result",
            code="github_demo.patch_metadata",
        )
    block_size = 256 * 1024
    chunks = []
    for index in range(0, actual_size, block_size):
        chunks.append(
            _capture(
                client,
                session_id,
                [
                    "dd",
                    f"if={path}",
                    f"bs={block_size}",
                    f"skip={index // block_size}",
                    "count=1",
                    "status=none",
                ],
            )
        )
    raw = b"".join(chunks)
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    if len(raw) != actual_size or digest != expected_digest:
        raise ResultIntegrityError(
            "Factory patch bytes failed verification",
            code="github_demo.patch_integrity",
        )
    assert_no_high_confidence_secrets(raw.decode("utf-8", "replace"))
    return PatchArtifact(data=raw, digest=digest, size_bytes=len(raw))


class FactoryRunExecutor:
    def __init__(
        self,
        config: ControllerConfig,
        *,
        client_factory: Callable[[], BaristaClient] | None = None,
        forge: GitHubForge | None = None,
    ):
        self.config = config
        self._client_factory = client_factory or (
            lambda: BaristaClient(Config.from_env())
        )
        self.forge = forge or GitHubForge(token=config.github_token)

    def execute(self, claim: Claim) -> dict:
        run = build_factory_run(self.config, claim)
        output = (
            self.config.result_directory.expanduser().resolve()
            / f"{run.name}.factory-result.json"
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        session = None
        with self._client_factory() as client:
            resolved = resolve_installed_app(client, self.config.factory_app)
            run_document = run.to_document()
            run_document.setdefault("metadata", {})["sh.barista.app-source"] = {
                "name": resolved.name,
                "version": resolved.version,
                "workload_digest": resolved.workload_digest,
                "manifest_digest": resolved.manifest_digest,
                "source": resolved.source,
                "source_revision": resolved.source_revision,
            }
            run = AppRun.parse(run_document)
            session, operation = client.launch_app_run(
                run,
                resolved.manifest_document(),
                install=False,
                # The endpoint is non-secret provider configuration needed by
                # coordinator apps that call their Host API. The provider still
                # injects the separately granted bearer credential.
                env={"BARISTA_HOST_API_ENDPOINT": client.config.endpoint},
            )
            collected = client.wait_app_run(
                run,
                session,
                operation,
                output=str(output),
                cleanup=False,
                timeout=1800,
                expected_identity={
                    "name": resolved.name,
                    "version": resolved.version,
                    "workload_digest": resolved.workload_digest,
                    "manifest_digest": resolved.manifest_digest,
                    "source": resolved.source,
                    "source_revision": resolved.source_revision,
                },
            )
            result = collected.result.to_document()
            if result["state"] != "succeeded":
                raise TerminalError(
                    "Factory did not verify the issue change",
                    code="github_demo.factory_failed",
                    details={"state": result["state"], "error": result.get("error")},
                    error_class="terminal",
                )
            delivery = run.deliveries["change"]
            pending = (
                (result.get("metadata") or {})
                .get("pending_deliveries", {})
                .get("change")
            )
            if (
                not isinstance(pending, dict)
                or pending.get("kind") != delivery.kind
                or pending.get("target") != delivery.target
                or pending.get("request_digest") != content_id(delivery.to_document())
            ):
                raise ResultIntegrityError(
                    "Factory result does not identify the declared runner delivery",
                    code="github_demo.delivery_identity",
                )
            patch_output = result.get("outputs", {}).get("patch")
            if not isinstance(patch_output, dict):
                raise ResultIntegrityError(
                    "Factory result has no patch", code="github_demo.patch_missing"
                )
            patch = read_verified_patch(
                client,
                session.id,
                uri=str(patch_output.get("uri", "")),
                expected_digest=str(patch_output.get("digest", "")),
                expected_size=int(
                    (patch_output.get("metadata") or {}).get("size_bytes", -1)
                ),
                max_bytes=self.config.max_patch_bytes,
                expected_run=run.name,
            )
            result_bindings = result.get("bindings", {})
            objective = result_bindings.get("objective") or {}
            objective_metadata = objective.get("metadata") or {}
            if (
                objective.get("kind") != "com.github.issue"
                or objective.get("uri") != claim.issue_uri
                or objective_metadata.get("repository_uri") != self.config.repository
                or objective_metadata.get("number") != claim.issue_number
            ):
                raise ResultIntegrityError(
                    "Factory result changed objective identity",
                    code="github_demo.objective_identity",
                )
            binding = result_bindings.get("workspace") or {}
            if (
                binding.get("kind") != "sh.barista.git.repository"
                or binding.get("uri") != self.config.repository
                or binding.get("requested_ref") != self.config.base_ref
            ):
                raise ResultIntegrityError(
                    "Factory result changed repository scope",
                    code="github_demo.repository_identity",
                )
            base_commit = str(binding.get("resolved_identity", ""))
            if not re.fullmatch(r"[0-9a-f]{40}", base_commit):
                raise ResultIntegrityError(
                    "Factory result has no exact Git base",
                    code="github_demo.base_missing",
                )
            repository_metadata = binding.get("metadata") or {}
            repository_size = int(repository_metadata.get("size_bytes", -1))
            submodules = str(repository_metadata.get("submodules", ""))
            lfs = str(repository_metadata.get("lfs", ""))
            if repository_size < 0 or submodules != "none" or lfs != "none":
                raise ResultIntegrityError(
                    "Factory result selected an unsupported repository graph",
                    code="github_demo.repository_graph",
                )
            repository = ResolvedGitRepository(
                uri=self.config.repository,
                requested_ref=self.config.base_ref,
                commit=base_commit,
                workspace=Path("/unavailable-in-runner"),
                size_bytes=repository_size,
                submodules=submodules,
                lfs=lfs,
            )
            change = deliver_draft_change(
                delivery,
                adapter=self.forge,
                repository=repository,
                run_state=result["state"],
                patch=patch,
                title=str(run.input_value["title"]),
                body=str(run.input_value["body"]),
            )
            factory_digest = collected.result.content_id()
            final = {
                "schema_version": "v1alpha1",
                "delivery_id": claim.delivery_id,
                "run": run.name,
                "issue": claim.issue_uri,
                "factory_result_digest": factory_digest,
                "base_commit": base_commit,
                "patch_digest": patch.digest,
                "draft": change.to_result_output(),
                "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            try:
                final["comment"] = self.forge.create_issue_comment(
                    claim.issue_uri,
                    f"Barista Factory opened draft pull request: {change.url}\n\nRun `{run.name}` · result `{factory_digest}`",
                )
            except TerminalError:
                final["comment"] = "not-created"
            final_path = (
                self.config.result_directory.expanduser().resolve() / f"{run.name}.json"
            )
            _atomic_write(final_path, canonical_bytes(final))
            cleanup = client.delete_session(
                session.id,
                idempotency_key=f"github-demo-cleanup-{run.content_id().split(':', 1)[1]}",
            )
            cleanup_operation = getattr(cleanup, "operation_id", None) or getattr(
                cleanup, "id", None
            )
            if cleanup_operation:
                client.wait_operation(cleanup_operation, timeout=120)
            return final
