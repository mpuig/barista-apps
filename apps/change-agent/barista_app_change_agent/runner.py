"""One bounded repository change App Run."""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Mapping

from barista_app_sdk import (
    AppRun,
    AppRunResult,
    BaristaClient,
    commit_workspace_branch,
    create_workspace_patch,
    materialize_git_repository,
    register_app_run_result,
    resolve_local_objective,
    validate_run,
)
from barista_app_sdk.content import content_id
from barista_app_sdk.errors import HostAPIError, InvalidRequestError

APP_NAME = "change-agent"
APP_VERSION = "0.1.0"
OPERATION = "change"
WORKSPACE_LIMIT = 256 * 1024 * 1024
PATCH_LIMIT = 16 * 1024 * 1024


def load_manifest() -> dict:
    path = resources.files("barista_app_change_agent").joinpath("manifest.json")
    return json.loads(path.read_text(encoding="utf-8"))


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _invalid(message: str, code: str) -> InvalidRequestError:
    return InvalidRequestError(message, code=code, error_class="invalid_request")


@dataclass(frozen=True)
class CommandReceipt:
    phase: str
    command_digest: str
    exit_code: int
    timed_out: bool
    duration_ms: int

    def document(self) -> dict:
        return {
            "phase": self.phase,
            "command_digest": self.command_digest,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "duration_ms": self.duration_ms,
        }


def _run_command(
    command: list[str],
    *,
    cwd: Path,
    timeout: int,
    phase: str,
    env: Mapping[str, str] | None = None,
) -> CommandReceipt:
    started = time.monotonic()
    timed_out = False
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=dict(env) if env is not None else os.environ.copy(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
        exit_code = completed.returncode
    except subprocess.TimeoutExpired:
        exit_code = 124
        timed_out = True
    except OSError:
        exit_code = 127
    elapsed = int((time.monotonic() - started) * 1000)
    return CommandReceipt(
        phase=phase,
        command_digest=content_id(command),
        exit_code=exit_code,
        timed_out=timed_out,
        duration_ms=elapsed,
    )


def _identity(run: AppRun, manifest: Mapping[str, Any]) -> dict:
    source = run.metadata.get("sh.barista.app-source", {})
    if not isinstance(source, Mapping):
        source = {}
    identity = {
        "name": source.get("name", manifest["name"]),
        "version": source.get("version", manifest["version"]),
        "workload_digest": source.get("workload_digest", manifest["workload"]["digest"]),
    }
    for field in ("manifest_digest", "source", "source_revision"):
        if source.get(field):
            identity[field] = source[field]
    return identity


def _effective_limit(client: BaristaClient, requested: int | None, *, hard_cap: int) -> int:
    limit = min(requested or hard_cap, hard_cap)
    discovery = client.negotiate(required=[])
    provider_limit = discovery.limits.get("max_binding_bytes")
    if isinstance(provider_limit, int) and provider_limit > 0:
        limit = min(limit, provider_limit)
    return limit


def _failed_result(
    run: AppRun,
    manifest: Mapping[str, Any],
    *,
    started_at: str,
    bindings: dict,
    outputs: dict,
    evidence: list,
    code: str,
    message: str,
) -> AppRunResult:
    return AppRunResult.parse(
        {
            "schema_version": "v1alpha1",
            "run": run.name,
            "app": run.app,
            "operation": run.operation,
            "state": "failed",
            "identity": _identity(run, manifest),
            "bindings": bindings,
            "outputs": outputs,
            "evidence": evidence,
            "error": {"code": code, "message": message},
            "started_at": started_at,
            "finished_at": _now(),
        }
    )


def execute_change_run(
    client: BaristaClient,
    run: AppRun,
    *,
    work_root: str | Path = "/work/app-runs",
) -> AppRunResult:
    """Execute and register one run; objective bytes never alter its policy."""
    manifest = load_manifest()
    operation = validate_run(run, manifest)
    if operation.name != OPERATION or run.app != f"{APP_NAME}@{APP_VERSION}":
        raise _invalid("App Run does not select this exact app operation", "change_agent.identity")

    started_at = _now()
    root = Path(work_root).expanduser().resolve() / run.name
    repository_path = root / "repository"
    patch_path = root / "change.patch"
    objective_path = root / "objective.txt"
    bindings: dict[str, dict] = {}
    outputs: dict[str, dict] = {}
    evidence: list[dict] = []
    receipts: list[CommandReceipt] = []

    workspace_binding = run.bindings["workspace"]
    if workspace_binding.credential is not None:
        result = _failed_result(
            run,
            manifest,
            started_at=started_at,
            bindings={},
            outputs={},
            evidence=[],
            code="change_agent.credential_unavailable",
            message="repository credential alias was not materialized for this app",
        )
        register_app_run_result(client, result)
        return result

    try:
        root.mkdir(parents=True, exist_ok=False)
        workspace_limit = _effective_limit(
            client,
            run.input_value.get("workspace_max_bytes"),
            hard_cap=WORKSPACE_LIMIT,
        )
        repository = materialize_git_repository(
            workspace_binding,
            repository_path,
            max_bytes=workspace_limit,
        )
        bindings["workspace"] = repository.to_result_binding()

        objective_binding = run.bindings.get("objective")
        if objective_binding is not None:
            objective = resolve_local_objective(objective_binding, max_bytes=1024 * 1024)
            objective_path.write_bytes(objective.content)
            bindings["objective"] = objective.to_result_binding()

        timeout = int(run.input_value.get("timeout_seconds", 600))
        command = list(run.input_value["command"])
        check = list(run.input_value["check"])
        process_env = os.environ.copy()
        if objective_binding is not None:
            process_env["BARISTA_OBJECTIVE_PATH"] = str(objective_path)
        # Objective content remains data in a file and can never replace
        # command/check, limits, or delivery policy.
        receipts.append(
            _run_command(
                command,
                cwd=repository.workspace,
                timeout=timeout,
                phase="change",
                env=process_env,
            )
        )
        if receipts[-1].exit_code == 0:
            receipts.append(
                _run_command(
                    check,
                    cwd=repository.workspace,
                    timeout=timeout,
                    phase="check",
                    env=process_env,
                )
            )

        patch = create_workspace_patch(
            repository.workspace,
            output=patch_path,
            max_bytes=min(int(run.input_value.get("patch_max_bytes", PATCH_LIMIT)), PATCH_LIMIT),
        )
        outputs["patch"] = patch.to_result_output()
        client.register_artifact(
            os.environ["BARISTA_APP_SESSION_ID"],
            name="change.patch",
            digest=patch.digest,
            size_bytes=patch.size_bytes,
            media_type="application/vnd.git.patch",
            idempotency_key=f"change-agent-{run.content_id().split(':', 1)[1]}-patch",
        )

        checks_passed = len(receipts) == 2 and all(receipt.exit_code == 0 for receipt in receipts)
        if checks_passed and run.input_value.get("branch"):
            branch = commit_workspace_branch(
                repository.workspace,
                base_commit=repository.commit,
                branch=str(run.input_value["branch"]),
                message=str(run.input_value.get("commit_message", "Apply verified change")),
            )
            outputs["branch"] = branch.to_result_output()

        evidence = [
            {
                "kind": "sh.barista.command-receipt",
                "digest": content_id(receipt.document()),
                "metadata": receipt.document(),
            }
            for receipt in receipts
        ]
        if not checks_passed:
            result = _failed_result(
                run,
                manifest,
                started_at=started_at,
                bindings=bindings,
                outputs=outputs,
                evidence=evidence,
                code="change_agent.check_failed",
                message="change command or declared check did not succeed",
            )
        else:
            result = AppRunResult.parse(
                {
                    "schema_version": "v1alpha1",
                    "run": run.name,
                    "app": run.app,
                    "operation": run.operation,
                    "state": "succeeded",
                    "identity": _identity(run, manifest),
                    "bindings": bindings,
                    "outputs": outputs,
                    "evidence": evidence,
                    "started_at": started_at,
                    "finished_at": _now(),
                }
            )
    except (HostAPIError, OSError, ValueError, subprocess.SubprocessError) as exc:
        code = getattr(exc, "code", "change_agent.execution_failed") or "change_agent.execution_failed"
        result = _failed_result(
            run,
            manifest,
            started_at=started_at,
            bindings=bindings,
            outputs=outputs,
            evidence=evidence,
            code=code,
            message=str(exc),
        )

    register_app_run_result(client, result)
    return result
