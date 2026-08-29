"""Controller-owned product planning, issue delivery, and final acceptance."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from pathlib import PurePosixPath
from urllib.parse import quote, unquote, urlparse

import httpx
from barista_app_sdk import (
    AppRun,
    BaristaClient,
    Config,
    GitHubForge,
    resolve_installed_app,
)
from barista_app_sdk.content import canonical_bytes
from barista_app_sdk.errors import ResultIntegrityError, TerminalError
from barista_app_sdk.sensitive import assert_no_high_confidence_secrets

from .config import ControllerConfig, planner_command_from_env
from .executor import _capture, _cleanup
from .program_protocol import FeaturePlan

_PROGRAM = re.compile(r"^[a-z0-9-]{1,160}$")
_FEATURE = re.compile(r"^[a-z0-9-]{1,40}$")

PROGRAM_ACCEPTANCE_SCRIPT = """import json
import subprocess
import sys
from pathlib import Path

manifest = json.loads(Path("product-manifest.json").read_text())
assert manifest["runtime"] == {"containers": 1, "port": 8080}
assert manifest["bindings"]["state"] == {"kind": "sqlite-state", "path": "/data", "writable": True}
dockerfile = Path("Dockerfile").read_text()
assert dockerfile.count("FROM ") == 2
assert "AS frontend" in dockerfile
assert "VOLUME [\\"/data\\"]" in dockerfile
assert "COPY --from=frontend /src/web/dist /app/web/dist" in dockerfile
server = Path("app/server.py").read_text()
assert all(route in server for route in ("/api/health", "/api/events"))
for name in ("index.html", "app.css", "app.js"):
    assert (Path("web/src") / name).read_bytes() == (Path("web/dist") / name).read_bytes()
subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], check=True)
"""


class GitHubProgramForge:
    """Bounded runner-owned GitHub side effects for product programs."""

    def __init__(
        self,
        *,
        token: str,
        repository: str,
        client: httpx.Client | None = None,
    ):
        if not token:
            raise ValueError("runtime forge token is required")
        self.repository = repository
        self.full_name = repository.removeprefix("https://github.com/")
        self._owned = client is None
        self._client = client or httpx.Client(
            base_url="https://api.github.com",
            timeout=30,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "barista-product-program",
            },
        )

    def close(self) -> None:
        if self._owned:
            self._client.close()

    def read_file(self, path: str, commit: str, *, max_bytes: int = 64 * 1024) -> bytes:
        if (
            path.startswith("/")
            or ".." in PurePosixPath(path).parts
            or len(path) > 512
            or re.fullmatch(r"[0-9a-f]{40}", commit) is None
        ):
            raise ValueError("BRD file identity is invalid")
        raw = bytearray()
        with self._client.stream(
            "GET",
            f"/repos/{self.full_name}/contents/{quote(path, safe='/')}",
            params={"ref": commit},
            headers={"Accept": "application/vnd.github.raw+json"},
        ) as response:
            if response.status_code != 200:
                raise TerminalError(
                    "approved BRD file could not be read",
                    code="github_program.brd_unavailable",
                    error_class="terminal",
                )
            length = response.headers.get("content-length")
            if length and int(length) > max_bytes:
                raise ResultIntegrityError(
                    "approved BRD bytes are invalid", code="github_program.brd_bytes"
                )
            for chunk in response.iter_bytes():
                if len(raw) + len(chunk) > max_bytes:
                    raise ResultIntegrityError(
                        "approved BRD bytes are invalid",
                        code="github_program.brd_bytes",
                    )
                raw.extend(chunk)
        if not raw or not raw.startswith(b"# BRD:"):
            raise ResultIntegrityError(
                "approved BRD bytes are invalid", code="github_program.brd_bytes"
            )
        document = bytes(raw)
        assert_no_high_confidence_secrets(document.decode("utf-8"))
        return document

    def ensure_feature_issue(
        self,
        *,
        program_id: str,
        feature: dict,
        plan_digest: str,
    ) -> dict:
        feature_id = str(feature["id"])
        if (
            _PROGRAM.fullmatch(program_id) is None
            or _FEATURE.fullmatch(feature_id) is None
            or re.fullmatch(r"sha256:[0-9a-f]{64}", plan_digest) is None
        ):
            raise ValueError("feature delivery identity is invalid")
        marker = (
            "<!-- barista-program-feature:v1 "
            f"program={program_id} feature={feature_id} plan={plan_digest} -->"
        )
        response = self._client.get(
            f"/repos/{self.full_name}/issues",
            params={"state": "all", "per_page": 100, "direction": "desc"},
        )
        if response.status_code != 200:
            raise TerminalError(
                "feature issue lookup failed",
                code="github_program.issue_lookup",
                error_class="terminal",
            )
        issues = response.json()
        if not isinstance(issues, list) or len(issues) > 100:
            raise ResultIntegrityError(
                "feature issue lookup is unbounded", code="github_program.issue_lookup"
            )
        matches = [
            issue
            for issue in issues
            if isinstance(issue, dict)
            and "pull_request" not in issue
            and marker in str(issue.get("body") or "")
        ]
        if len(matches) > 1:
            raise ResultIntegrityError(
                "feature issue identity is duplicated",
                code="github_program.issue_duplicate",
            )
        if matches:
            return matches[0]
        criteria = "\n".join(
            f"- {criterion}" for criterion in feature["acceptance_criteria"]
        )
        dependencies = ", ".join(feature["dependencies"]) or "none"
        body = (
            f"{marker}\n\n"
            f"Program: `{program_id}`\n\n"
            f"## Summary\n\n{feature['summary']}\n\n"
            f"## Dependencies\n\n{dependencies}\n\n"
            f"## Acceptance\n\n{criteria}\n\n"
            "This issue is inert plan data. It cannot change trusted commands, credentials, repository scope, base, checks, or delivery policy."
        )
        assert_no_high_confidence_secrets(body)
        created = self._client.post(
            f"/repos/{self.full_name}/issues",
            json={"title": f"[Feature] {feature['title']}", "body": body},
        )
        if created.status_code != 201:
            raise TerminalError(
                "feature issue creation failed",
                code="github_program.issue_create",
                error_class="terminal",
            )
        return created.json()


class ProgramRunExecutor:
    def __init__(
        self,
        config: ControllerConfig,
        *,
        client_factory: Callable[[], BaristaClient] | None = None,
        ref_forge: GitHubForge | None = None,
    ):
        self.config = config
        self._client_factory = client_factory or (
            lambda: BaristaClient(Config.from_env())
        )
        self.ref_forge = ref_forge or GitHubForge(token=config.github_token)

    def plan(self, program: dict) -> tuple[dict, str]:
        program_id = str(program["program_id"])
        approved = str(program["brd"]["approved_commit"])
        run = AppRun.parse(
            {
                "schema_version": "v1alpha1",
                "name": f"{program_id}-feature-plan",
                "app": self.config.factory_app,
                "operation": "feature-plan",
                "input": {
                    "media_type": "application/json",
                    "value": {
                        "planner_app": self.config.planner_app,
                        "planner": {"command": planner_command_from_env()},
                        "program": program_id,
                        "approved_commit": approved,
                        "brd_path": str(program["brd"]["path"]),
                        "brd_digest": str(program["brd"]["digest"]),
                        "timeout_seconds": 600,
                    },
                },
                "bindings": {
                    "workspace": {
                        "kind": "sh.barista.git.repository",
                        "uri": self.config.repository,
                        "ref": self.config.base_ref,
                    }
                },
            }
        )
        document, session_id, selected_run = self._run(run)
        if document["state"] != "succeeded":
            raise TerminalError(
                "Factory feature planning failed",
                code="github_program.plan_failed",
                error_class="terminal",
            )
        binding = document.get("bindings", {}).get("workspace", {})
        if binding.get("resolved_identity") != approved:
            raise ResultIntegrityError(
                "planning result changed approved commit",
                code="github_program.plan_commit",
            )
        output = document.get("outputs", {}).get("plan", {})
        raw = self._read_result_file(
            run,
            output,
            "feature-plan.json",
            64 * 1024,
            session_id=session_id,
            selected_run=selected_run,
        )
        plan = FeaturePlan.parse_bytes(raw)
        if plan.program != program_id or plan.approved_commit != approved:
            raise ResultIntegrityError(
                "planning result changed program identity",
                code="github_program.plan_identity",
            )
        return plan.to_document(), plan.content_id()

    def accept(self, program: dict) -> dict:
        program_id = str(program["program_id"])
        assembled = self.ref_forge.resolve_ref(
            self.config.repository, self.config.base_ref
        )
        features = [str(feature["id"]) for feature in program["features"]]
        run = AppRun.parse(
            {
                "schema_version": "v1alpha1",
                "name": f"{program_id}-acceptance",
                "app": self.config.factory_app,
                "operation": "program-acceptance",
                "input": {
                    "media_type": "application/json",
                    "value": {
                        "program": program_id,
                        "assembled_commit": assembled,
                        "features": features,
                        "acceptance": {
                            "command": ["python", ".barista/accept_program.py"],
                            "files": {
                                ".barista/accept_program.py": PROGRAM_ACCEPTANCE_SCRIPT
                            },
                        },
                        "timeout_seconds": 600,
                    },
                },
                "bindings": {
                    "workspace": {
                        "kind": "sh.barista.git.repository",
                        "uri": self.config.repository,
                        "ref": self.config.base_ref,
                    }
                },
            }
        )
        document, session_id, selected_run = self._run(run)
        if document["state"] != "succeeded":
            raise TerminalError(
                "assembled program failed acceptance",
                code="github_program.acceptance_failed",
                error_class="terminal",
            )
        binding = document.get("bindings", {}).get("workspace", {})
        if binding.get("resolved_identity") != assembled:
            raise ResultIntegrityError(
                "acceptance result changed assembled commit",
                code="github_program.acceptance_commit",
            )
        output = document.get("outputs", {}).get("result", {})
        raw = self._read_result_file(
            run,
            output,
            "program-acceptance.json",
            64 * 1024,
            session_id=session_id,
            selected_run=selected_run,
        )
        report = json.loads(raw)
        if (
            report.get("program") != program_id
            or report.get("assembled_commit") != assembled
            or report.get("features") != features
            or report.get("accepted") is not True
            or report.get("exit_code") != 0
            or raw != canonical_bytes(report)
        ):
            raise ResultIntegrityError(
                "program acceptance report is invalid",
                code="github_program.acceptance_report",
            )
        return report

    def _run(self, run: AppRun) -> tuple[dict, str, AppRun]:
        output = (
            self.config.result_directory.expanduser().resolve()
            / f"{run.name}.factory-result.json"
        )
        output.parent.mkdir(parents=True, exist_ok=True)
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
            selected = AppRun.parse(run_document)
            session, operation = client.launch_app_run(
                selected,
                resolved.manifest_document(),
                install=False,
                env={"BARISTA_HOST_API_ENDPOINT": client.config.endpoint},
            )
            collected = client.wait_app_run(
                selected,
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
            return collected.result.to_document(), session.id, selected

    def _read_result_file(
        self,
        run: AppRun,
        output: dict,
        filename: str,
        max_bytes: int,
        *,
        session_id: str,
        selected_run: AppRun,
    ) -> bytes:
        # `_run` deliberately leaves the successful owner available for this
        # bounded handoff. The client context has closed, so reacquire a client
        # under the same controller authority before cleanup.
        uri = str(output.get("uri", ""))
        parsed = urlparse(uri)
        path = PurePosixPath(unquote(parsed.path))
        expected = PurePosixPath("/work/program-runs") / run.name / filename
        size = int((output.get("metadata") or {}).get("size_bytes", -1))
        digest = str(output.get("digest", ""))
        if (
            parsed.scheme != "file"
            or parsed.netloc
            or path != expected
            or not 1 <= size <= max_bytes
            or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None
        ):
            raise ResultIntegrityError(
                "program output identity is invalid",
                code="github_program.output_identity",
            )
        with self._client_factory() as client:
            raw = _capture(
                client,
                session_id,
                ["cat", str(path)],
                max_output_bytes=size,
            )
            if (
                len(raw) != size
                or "sha256:" + hashlib.sha256(raw).hexdigest() != digest
            ):
                raise ResultIntegrityError(
                    "program output bytes failed verification",
                    code="github_program.output_integrity",
                )
            assert_no_high_confidence_secrets(raw.decode("utf-8"))
            _cleanup(client, session_id, selected_run)
            return raw
