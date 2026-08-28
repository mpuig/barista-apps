"""Factory's repository-backed coordinating software-change operation."""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import pwd
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Mapping

import barista_app_sdk.lifecycle as app_lifecycle
from barista_app_sdk import (
    AppRun,
    AppRunResult,
    BaristaClient,
    ForgeAdapter,
    commit_workspace_branch,
    create_workspace_patch,
    deliver_draft_change,
    materialize_git_repository,
    register_app_run_result,
    resolve_issue_objective,
    resolve_local_objective,
    validate_run,
)
from barista_app_sdk.content import canonical_bytes, content_id
from barista_app_sdk.errors import HostAPIError, InvalidRequestError, TerminalError
from barista_app_sdk.forge import GITHUB_ISSUE_KIND
from barista_app_sdk.sources import LOCAL_TEXT_KINDS
from barista_app_sdk.sensitive import assert_no_high_confidence_secrets

from . import transfer

OPERATION = "software-change"
WORKSPACE_LIMIT = 256 * 1024 * 1024
PATCH_LIMIT = 16 * 1024 * 1024
# Objective delivery uses Factory's small, event-based transfer helper rather
# than pretending argv is a repository/blob transport.
OBJECTIVE_LIMIT = 64 * 1024
WORKER_PROJECT = "/work/project"
WORKER_OBJECTIVE = "/tmp/barista-objective.txt"
WORKER_PATCH = "/tmp/barista-worker.patch"
_BRANCH = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._/-]*[A-Za-z0-9])?$")


def load_manifest() -> dict:
    path = resources.files("barista_app_factory").joinpath("manifest.json")
    return json.loads(path.read_text(encoding="utf-8"))


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _invalid(message: str, code: str) -> InvalidRequestError:
    return InvalidRequestError(message, code=code, error_class="invalid_request")


def _terminal(message: str, code: str, *, details: dict | None = None) -> TerminalError:
    return TerminalError(message, code=code, details=details or {}, error_class="terminal")


@dataclass(frozen=True)
class WorkerOutcome:
    task: str
    worker: str | None
    state: str
    exit_code: int
    patch: bytes | None
    patch_digest: str | None
    error: str | None = None

    def receipt(self) -> dict:
        document = {
            "task": self.task,
            "worker": self.worker,
            "state": self.state,
            "exit_code": self.exit_code,
            "patch_digest": self.patch_digest,
        }
        if self.error:
            document["error"] = self.error
        return document


def _identity(run: AppRun, manifest: Mapping[str, Any]) -> dict:
    source = run.metadata.get("sh.barista.app-source", {})
    if not isinstance(source, Mapping):
        source = {}
    result = {
        "name": source.get("name", manifest["name"]),
        "version": source.get("version", manifest["version"]),
        "workload_digest": source.get("workload_digest", manifest["workload"]["digest"]),
    }
    for field in ("manifest_digest", "source", "source_revision"):
        if source.get(field):
            result[field] = source[field]
    return result


def _effective_workspace_limit(client: BaristaClient, requested: int | None) -> int:
    limit = min(requested or WORKSPACE_LIMIT, WORKSPACE_LIMIT)
    provider = client.negotiate(required=[]).limits.get("max_binding_bytes")
    if isinstance(provider, int) and provider > 0:
        limit = min(limit, provider)
    return limit


def _exec_exit(
    client: BaristaClient,
    session_id: str,
    command: list[str],
    *,
    timeout: int,
    env: dict[str, str] | None = None,
    working_dir: str | None = None,
) -> int:
    handle = client.exec(
        session_id,
        command,
        env=env,
        working_dir=working_dir,
        timeout_seconds=timeout,
    )
    operation = client.wait_operation(handle.operation_id, timeout=timeout)
    return int((operation.result or {}).get("exit_code", 1))


def _worker(
    client: BaristaClient,
    *,
    run: AppRun,
    worker_app: str,
    task: Mapping[str, Any],
    repository_uri: str,
    commit: str,
    objective: bytes,
    objective_uri: str,
    timeout: int,
    patch_limit: int,
    owner: str,
    lfs: str,
) -> WorkerOutcome:
    task_id = str(task["id"])
    worker_id: str | None = None
    try:
        session = client.ensure_session(
            worker_app,
            name=f"{run.name}-{task_id}",
            metadata={"role": "factory-change-worker", "run": run.name, "task": task_id},
            idempotency_key=f"{run.content_id()}:{task_id}:worker",
        )
        worker_id = session.id
        clone_env = {"GIT_TERMINAL_PROMPT": "0"}
        if lfs == "pointer-files":
            clone_env["GIT_LFS_SKIP_SMUDGE"] = "1"
        clone_exit = _exec_exit(
            client,
            worker_id,
            ["git", "clone", "--no-checkout", "--filter=blob:none", "--", repository_uri, WORKER_PROJECT],
            timeout=timeout,
            env=clone_env,
        )
        if clone_exit != 0:
            raise _terminal("worker could not clone repository", "factory.worker_clone")
        checkout_exit = _exec_exit(
            client,
            worker_id,
            ["git", "-C", WORKER_PROJECT, "checkout", "--detach", commit],
            timeout=timeout,
            env=clone_env,
        )
        if checkout_exit != 0:
            raise _terminal(
                "worker could not check out the coordinator-selected commit",
                "factory.worker_base_unavailable",
            )
        transfer.write_file(client, worker_id, WORKER_OBJECTIVE, objective)
        command_exit = _exec_exit(
            client,
            worker_id,
            list(task["command"]),
            timeout=timeout,
            env={
                "BARISTA_OBJECTIVE_PATH": WORKER_OBJECTIVE,
                "BARISTA_OBJECTIVE_URI": objective_uri,
                "BARISTA_BASE_COMMIT": commit,
            },
            working_dir=WORKER_PROJECT,
        )
        if command_exit != 0:
            return _record_worker(
                client,
                owner=owner,
                run=run,
                outcome=WorkerOutcome(
                    task=task_id,
                    worker=worker_id,
                    state="failed",
                    exit_code=command_exit,
                    patch=None,
                    patch_digest=None,
                    error="worker command failed",
                ),
            )
        patch_exit = _exec_exit(
            client,
            worker_id,
            [
                "sh",
                "-c",
                "set -e; git -C /work/project add -A; "
                "git -C /work/project diff --cached --binary --no-ext-diff --full-index -- > /tmp/barista-worker.patch",
            ],
            timeout=timeout,
        )
        if patch_exit != 0:
            raise _terminal("worker could not create patch", "factory.worker_patch")
        patch = transfer.read_file_bounded(
            client,
            worker_id,
            WORKER_PATCH,
            max_bytes=patch_limit,
        )
        assert_no_high_confidence_secrets(patch.decode("utf-8", "replace"))
        digest = "sha256:" + hashlib.sha256(patch).hexdigest()
        return _record_worker(
            client,
            owner=owner,
            run=run,
            outcome=WorkerOutcome(
                task=task_id,
                worker=worker_id,
                state="succeeded",
                exit_code=0,
                patch=patch,
                patch_digest=digest,
            ),
        )
    except (HostAPIError, transfer.TransferError, OSError, ValueError) as exc:
        outcome = WorkerOutcome(
            task=task_id,
            worker=worker_id,
            state="failed",
            exit_code=1,
            patch=None,
            patch_digest=None,
            error=str(exc),
        )
        try:
            return _record_worker(client, owner=owner, run=run, outcome=outcome)
        except HostAPIError:
            return outcome


def _record_worker(
    client: BaristaClient,
    *,
    owner: str,
    run: AppRun,
    outcome: WorkerOutcome,
) -> WorkerOutcome:
    receipt = outcome.receipt()
    blob = canonical_bytes(receipt)
    client.register_artifact(
        owner,
        name=f"software-change-{outcome.task}-receipt.json",
        digest=content_id(receipt),
        size_bytes=len(blob),
        media_type="application/vnd.barista.factory.change-receipt+json",
        idempotency_key=f"{run.content_id()}:{outcome.task}:receipt",
    )
    return outcome


def _run_local(command: list[str], *, cwd: Path, timeout: int) -> int:
    # Repository code under test is untrusted. In particular, it must not inherit
    # the coordinator's Host API grant or forge credentials. The production
    # image runs as root, so execute checks as the unprivileged `nobody` account;
    # developer test runs already execute as an unprivileged user.
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": "/tmp",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "CI": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    identity: dict[str, int] = {}
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        nobody = pwd.getpwnam("nobody")
        identity = {"user": nobody.pw_uid, "group": nobody.pw_gid}
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
            **identity,
        ).returncode
    except subprocess.TimeoutExpired:
        return 124
    except OSError:
        return 127


def _safe_acceptance_file(root: Path, relative: str) -> Path:
    if Path(relative).is_absolute():
        raise _invalid("acceptance file path must be repository-relative", "factory.acceptance_path")
    target = (root / relative).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise _invalid("acceptance file escapes the integration workspace", "factory.acceptance_path") from exc
    return target


def _apply_patch(root: Path, patch: bytes) -> None:
    # A byte-for-byte copied clean checkout carries index stat metadata from the
    # base path. Refresh it at the integration path before asking `--index` to
    # compare worktree and index; content identity remains unchanged.
    refreshed = subprocess.run(
        ["git", "-C", str(root), "update-index", "--refresh"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=120,
        check=False,
    )
    if refreshed.returncode != 0:
        raise _terminal("could not refresh integration index", "factory.integration_index")
    process = subprocess.run(
        ["git", "-C", str(root), "-c", f"core.hooksPath={os.devnull}", "apply", "--index", "-"],
        input=patch,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=120,
        check=False,
    )
    if process.returncode != 0:
        raise _terminal("worker patches did not integrate cleanly", "factory.integration_conflict")


def _replay_terminal_result(client: BaristaClient, run: AppRun) -> AppRunResult | None:
    """Converge an owning-session retry on its already canonical terminal result."""
    path = Path(app_lifecycle.APP_RUN_RESULT_PATH)
    if not path.is_file():
        return None
    try:
        raw = path.read_bytes()
        result = AppRunResult.parse(json.loads(raw))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, InvalidRequestError):
        return None
    document = result.to_document()
    if (
        document["run"] != run.name
        or document["app"] != run.app
        or document["operation"] != run.operation
        or raw != result.canonical_bytes()
    ):
        return None
    register_app_run_result(client, result)
    return result


def execute_software_change(
    client: BaristaClient,
    run: AppRun,
    *,
    forge: ForgeAdapter | None = None,
    work_root: str | Path = "/work/app-runs",
    register_result: bool = True,
    objective_context: Mapping[str, Any] | None = None,
) -> AppRunResult:
    """Coordinate isolated patches, integrate, independently check, and deliver."""
    manifest = load_manifest()
    operation = validate_run(run, manifest)
    selected_name, separator, selected_version = run.app.rpartition("@")
    if (
        operation.name != OPERATION
        or not separator
        or not selected_name
        or selected_version != manifest["version"]
    ):
        raise _invalid("App Run does not select Factory software-change", "factory.run_identity")
    owner = os.environ.get("BARISTA_APP_SESSION_ID")
    if not owner:
        raise _invalid("provider did not inject BARISTA_APP_SESSION_ID", "factory.owner_missing")
    replayed = _replay_terminal_result(client, run)
    if replayed is not None:
        return replayed

    # Operation-level preflight happens before child-session mutation. Objective
    # text never participates in these authority and publication decisions.
    tasks = list(run.input_value["tasks"])
    if len({task["id"] for task in tasks}) != len(tasks):
        raise _invalid("software-change task ids must be unique", "factory.task_ids")
    workspace_binding = run.bindings["workspace"]
    objective_binding = run.bindings["objective"]
    delivery = run.deliveries.get("change")
    if workspace_binding.credential is not None:
        raise _invalid(
            "repository credential alias was not materialized for Factory",
            "factory.repository_credential_unavailable",
        )
    delivery_executor = (
        str(delivery.options.get("executor", "factory")) if delivery is not None else "factory"
    )
    if delivery_executor not in {"factory", "runner"}:
        raise _invalid("delivery executor must be 'factory' or 'runner'", "factory.delivery_executor")
    forge_is_needed = objective_binding.kind == GITHUB_ISSUE_KIND or (
        delivery is not None and delivery_executor == "factory"
    )
    if forge_is_needed and forge is None:
        raise _invalid("forge objective or delivery requires a forge adapter", "factory.forge_missing")
    if delivery is not None and delivery.target != workspace_binding.uri:
        raise _invalid("delivery target is outside the bound repository", "factory.delivery_scope")
    branch = str(run.input_value.get("branch", ""))
    delivery_branch = str(delivery.options.get("head_branch", "")) if delivery else ""
    if delivery is not None and not delivery_branch:
        raise _invalid("draft delivery requires head_branch", "factory.delivery_branch")
    for selected in (branch, delivery_branch):
        if selected and (
            not _BRANCH.fullmatch(selected) or selected.startswith("-") or ".." in selected
        ):
            raise _invalid("software-change requires safe branch names", "factory.branch")
    if branch and delivery_branch and branch != delivery_branch:
        raise _invalid(
            "local branch and delivery head_branch must match",
            "factory.branch_mismatch",
        )

    started_at = _now()
    root = Path(work_root).expanduser().resolve() / run.name
    base_path = root / "base"
    integration_path = root / "integration"
    patch_path = root / "integrated.patch"
    bindings: dict[str, dict] = {}
    outputs: dict[str, dict] = {}
    evidence: list[dict] = []
    error: tuple[str, str] | None = None
    outcomes: list[WorkerOutcome] = []

    try:
        root.mkdir(parents=True, exist_ok=False)
        repository = materialize_git_repository(
            workspace_binding,
            base_path,
            max_bytes=_effective_workspace_limit(
                client, run.input_value.get("workspace_max_bytes")
            ),
        )
        bindings["workspace"] = repository.to_result_binding()

        if objective_binding.kind in LOCAL_TEXT_KINDS:
            objective = resolve_local_objective(objective_binding, max_bytes=OBJECTIVE_LIMIT)
            objective_bytes = objective.content
            bindings["objective"] = objective.to_result_binding()
        elif objective_binding.kind == GITHUB_ISSUE_KIND:
            if forge is None:
                raise _invalid("forge issue objective requires a forge adapter", "factory.forge_missing")
            issue = resolve_issue_objective(objective_binding, forge, max_bytes=OBJECTIVE_LIMIT)
            if issue.repository_uri != repository.uri:
                raise _invalid(
                    "issue objective belongs to a different repository",
                    "factory.objective_repository_scope",
                )
            objective_content = dict(issue.objective()["content"])
            if objective_context is not None:
                objective_content["factory_context"] = dict(objective_context)
            objective_bytes = canonical_bytes(objective_content)
            if len(objective_bytes) > OBJECTIVE_LIMIT:
                raise _invalid(
                    "resolved objective context exceeds the supported bound",
                    "factory.objective_too_large",
                )
            bindings["objective"] = issue.to_result_binding()
        else:  # validate_run closes this, kept fail-closed for direct callers.
            raise _invalid("unsupported objective binding", "factory.objective_kind")

        timeout = int(run.input_value.get("timeout_seconds", 600))
        patch_limit = min(int(run.input_value.get("patch_max_bytes", PATCH_LIMIT)), PATCH_LIMIT)
        concurrency = min(int(run.input_value.get("concurrency", 4)), len(tasks), 16)
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {
                pool.submit(
                    _worker,
                    client,
                    run=run,
                    worker_app=str(run.input_value["worker_app"]),
                    task=task,
                    repository_uri=repository.uri,
                    commit=repository.commit,
                    objective=objective_bytes,
                    objective_uri=objective_binding.uri,
                    timeout=timeout,
                    patch_limit=patch_limit,
                    owner=owner,
                    lfs=repository.lfs,
                ): task["id"]
                for task in tasks
            }
            completed = [future.result() for future in futures]
            by_id = {outcome.task: outcome for outcome in completed}
        outcomes = [by_id[task["id"]] for task in tasks]
        patch_directory = root / "worker-patches"
        patch_directory.mkdir(parents=True, exist_ok=True)
        for outcome in outcomes:
            receipt_evidence = {
                "kind": "sh.barista.factory.change-receipt",
                "digest": content_id(outcome.receipt()),
                "metadata": outcome.receipt(),
            }
            if outcome.patch is not None and outcome.patch_digest is not None:
                worker_patch_path = patch_directory / f"{outcome.task}.patch"
                worker_patch_path.write_bytes(outcome.patch)
                receipt_evidence["uri"] = worker_patch_path.as_uri()
                client.register_artifact(
                    owner,
                    name=f"software-change-{outcome.task}.patch",
                    digest=outcome.patch_digest,
                    size_bytes=len(outcome.patch),
                    media_type="application/vnd.git.patch",
                    idempotency_key=f"{run.content_id()}:{outcome.task}:patch",
                )
            evidence.append(receipt_evidence)
            # Harvest is now durable in the owning session, so successful worker
            # compute can disappear. Failed workers remain for forensics.
            if outcome.state == "succeeded" and outcome.worker:
                try:
                    client.delete_session(
                        outcome.worker,
                        idempotency_key=f"{run.content_id()}:{outcome.task}:reap",
                    )
                except HostAPIError:
                    pass
        failed = [outcome.task for outcome in outcomes if outcome.state != "succeeded"]
        if failed:
            raise _terminal(
                "one or more software-change workers failed",
                "factory.worker_failed",
                details={"tasks": failed},
            )

        shutil.copytree(repository.workspace, integration_path, symlinks=True)
        for outcome in outcomes:
            assert outcome.patch is not None
            _apply_patch(integration_path, outcome.patch)

        acceptance = run.input_value["acceptance"]
        for relative, content in acceptance.get("files", {}).items():
            target = _safe_acceptance_file(integration_path, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        acceptance_exit = _run_local(
            list(acceptance["command"]), cwd=integration_path, timeout=timeout
        )
        acceptance_receipt = {
            "phase": "integration-acceptance",
            "command_digest": content_id(list(acceptance["command"])),
            "exit_code": acceptance_exit,
        }
        evidence.append(
            {
                "kind": "sh.barista.factory.integration-check",
                "digest": content_id(acceptance_receipt),
                "metadata": acceptance_receipt,
            }
        )
        if acceptance_exit != 0:
            raise _terminal("integrated change failed coordinator acceptance", "factory.acceptance_failed")

        integrated = create_workspace_patch(
            integration_path,
            output=patch_path,
            max_bytes=patch_limit,
        )
        outputs["patch"] = integrated.to_result_output()
        client.register_artifact(
            owner,
            name="integrated-change.patch",
            digest=integrated.digest,
            size_bytes=integrated.size_bytes,
            media_type="application/vnd.git.patch",
            idempotency_key=f"{run.content_id()}:integrated-patch",
        )
        if run.input_value.get("branch"):
            branch = commit_workspace_branch(
                integration_path,
                base_commit=repository.commit,
                branch=str(run.input_value["branch"]),
                message=str(run.input_value.get("commit_message", "Apply verified Factory change")),
            )
            outputs["branch"] = branch.to_result_output()

        if delivery is not None and delivery_executor == "factory":
            assert forge is not None  # established by mutation-free preflight
            identity = _identity(run, manifest)
            receipt_digests = sorted(
                item["digest"]
                for item in evidence
                if item["kind"] == "sh.barista.factory.change-receipt"
            )
            verification = "\n".join(
                [
                    "",
                    "---",
                    "Barista Factory verification",
                    f"Objective: {bindings['objective']['uri']}@{bindings['objective']['resolved_identity']}",
                    f"Base commit: {repository.commit}",
                    f"Head branch: {delivery.options.get('head_branch', '')}",
                    f"App: {run.app}",
                    f"Workload: {identity['workload_digest']}",
                    f"Integration check: {content_id(acceptance_receipt)}",
                    f"Integrated patch: {integrated.digest}",
                    "Worker receipts: " + ", ".join(receipt_digests),
                ]
            )
            change = deliver_draft_change(
                delivery,
                adapter=forge,
                repository=repository,
                run_state="succeeded",
                patch=integrated,
                title=str(run.input_value.get("title", "Verified Factory change")),
                body=str(run.input_value.get("body", "Verified by Factory integration acceptance."))
                + verification,
            )
            outputs["change"] = change.to_result_output()
    except (HostAPIError, transfer.TransferError, OSError, ValueError, subprocess.SubprocessError) as exc:
        error = (
            getattr(exc, "code", "factory.software_change_failed") or "factory.software_change_failed",
            str(exc),
        )

    state = "failed" if error else "succeeded"
    document = {
        "schema_version": "v1alpha1",
        "run": run.name,
        "app": run.app,
        "operation": run.operation,
        "state": state,
        "identity": _identity(run, manifest),
        "bindings": bindings,
        "outputs": outputs,
        "evidence": evidence,
        "started_at": started_at,
        "finished_at": _now(),
        "metadata": {
            "workers": {
                outcome.task: {
                    "session": outcome.worker,
                    "state": outcome.state,
                    "patch_digest": outcome.patch_digest,
                }
                for outcome in outcomes
            },
            "pending_deliveries": (
                {
                    "change": {
                        "kind": delivery.kind,
                        "target": delivery.target,
                        "request_digest": content_id(delivery.to_document()),
                    }
                }
                if error is None and delivery is not None and delivery_executor == "runner"
                else {}
            ),
        },
    }
    if error:
        document["error"] = {"code": error[0], "message": error[1]}
    result = AppRunResult.parse(document)
    if register_result:
        register_app_run_result(client, result)
    return result
