"""Closed product-program planning and final-acceptance Factory operations."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from barista_app_sdk import (
    AppRun,
    AppRunResult,
    BaristaClient,
    materialize_git_repository,
    register_app_run_result,
    validate_run,
)
from barista_app_sdk.content import canonical_bytes, content_id
from barista_app_sdk.errors import HostAPIError, InvalidRequestError
from barista_app_sdk.sensitive import assert_no_high_confidence_secrets

from . import transfer
from .software_change import (
    OBJECTIVE_LIMIT,
    WORKER_PROJECT,
    _effective_workspace_limit,
    _exec_exit,
    _identity,
    _invalid,
    _replay_terminal_result,
    _run_local,
    _safe_acceptance_file,
    _terminal,
    load_manifest,
)

MAX_PLAN_BYTES = 64 * 1024
MAX_FEATURES = 16
_FEATURE_ID = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
PLANNER_OBJECTIVE = "/tmp/barista-program-objective.json"
PLANNER_RESULT = "/tmp/barista-feature-plan.json"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class FeaturePlanError(ValueError):
    """Planner output is malformed, unbounded, non-canonical, or cyclic."""


@dataclass(frozen=True)
class PlannedFeature:
    id: str
    title: str
    summary: str
    acceptance_criteria: tuple[str, ...]
    dependencies: tuple[str, ...]

    def to_document(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "acceptance_criteria": list(self.acceptance_criteria),
            "dependencies": list(self.dependencies),
        }


@dataclass(frozen=True)
class FeaturePlan:
    program: str
    approved_commit: str
    features: tuple[PlannedFeature, ...]

    @classmethod
    def parse_bytes(cls, raw: bytes) -> FeaturePlan:
        if not raw or len(raw) > MAX_PLAN_BYTES:
            raise FeaturePlanError("feature plan size is outside the supported bound")
        try:
            text = raw.decode("utf-8")
            document = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FeaturePlanError("feature plan must be UTF-8 JSON") from exc
        if not isinstance(document, dict) or set(document) != {
            "schema_version",
            "program",
            "approved_commit",
            "features",
        }:
            raise FeaturePlanError("feature plan has invalid fields")
        if document.get("schema_version") != "v1alpha1":
            raise FeaturePlanError("feature plan schema is unsupported")
        program = document.get("program")
        commit = document.get("approved_commit")
        values = document.get("features")
        if (
            not isinstance(program, str)
            or not program
            or len(program) > 160
            or not isinstance(commit, str)
            or _SHA.fullmatch(commit) is None
            or not isinstance(values, list)
            or not 1 <= len(values) <= MAX_FEATURES
        ):
            raise FeaturePlanError("feature plan identity is invalid")
        features: list[PlannedFeature] = []
        identities: set[str] = set()
        for value in values:
            if not isinstance(value, dict) or set(value) != {
                "id",
                "title",
                "summary",
                "acceptance_criteria",
                "dependencies",
            }:
                raise FeaturePlanError("feature entry has invalid fields")
            feature_id = value.get("id")
            title = value.get("title")
            summary = value.get("summary")
            criteria = value.get("acceptance_criteria")
            dependencies = value.get("dependencies")
            if (
                not isinstance(feature_id, str)
                or _FEATURE_ID.fullmatch(feature_id) is None
                or feature_id in identities
                or not isinstance(title, str)
                or not title.strip()
                or len(title) > 200
                or not isinstance(summary, str)
                or not summary.strip()
                or len(summary) > 4000
                or not isinstance(criteria, list)
                or not 1 <= len(criteria) <= 12
                or any(
                    not isinstance(item, str) or not item.strip() or len(item) > 1000
                    for item in criteria
                )
                or not isinstance(dependencies, list)
                or len(dependencies) > MAX_FEATURES - 1
                or any(
                    not isinstance(item, str) or _FEATURE_ID.fullmatch(item) is None
                    for item in dependencies
                )
                or len(set(dependencies)) != len(dependencies)
            ):
                raise FeaturePlanError("feature entry is invalid")
            identities.add(feature_id)
            features.append(
                PlannedFeature(
                    feature_id,
                    title,
                    summary,
                    tuple(criteria),
                    tuple(dependencies),
                )
            )
        if any(
            dependency not in identities or dependency == feature.id
            for feature in features
            for dependency in feature.dependencies
        ):
            raise FeaturePlanError("feature plan has an unknown or self dependency")
        graph = {feature.id: feature.dependencies for feature in features}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visiting:
                raise FeaturePlanError("feature dependency graph contains a cycle")
            if node in visited:
                return
            visiting.add(node)
            for dependency in graph[node]:
                visit(dependency)
            visiting.remove(node)
            visited.add(node)

        for identity in graph:
            visit(identity)
        if raw != canonical_bytes(document):
            raise FeaturePlanError("feature plan is not canonical JSON")
        assert_no_high_confidence_secrets(text)
        return cls(program, commit, tuple(features))

    def to_document(self) -> dict:
        return {
            "schema_version": "v1alpha1",
            "program": self.program,
            "approved_commit": self.approved_commit,
            "features": [feature.to_document() for feature in self.features],
        }

    def content_id(self) -> str:
        return content_id(self.to_document())


def _validate_operation(run: AppRun, name: str) -> tuple[Mapping[str, Any], str]:
    manifest = load_manifest()
    operation = validate_run(run, manifest)
    selected_name, separator, selected_version = run.app.rpartition("@")
    if (
        operation.name != name
        or not separator
        or not selected_name
        or selected_version != manifest["version"]
    ):
        raise _invalid(
            f"App Run does not select Factory {name}", "factory.run_identity"
        )
    owner = os.environ.get("BARISTA_APP_SESSION_ID")
    if not owner:
        raise _invalid(
            "provider did not inject BARISTA_APP_SESSION_ID", "factory.owner_missing"
        )
    return manifest, owner


def _repository_binding(client: BaristaClient, run: AppRun, root: Path):
    binding = run.bindings["workspace"]
    if binding.credential is not None:
        raise _invalid(
            "repository credential alias was not materialized for Factory",
            "factory.repository_credential_unavailable",
        )
    return materialize_git_repository(
        binding,
        root,
        max_bytes=_effective_workspace_limit(
            client, run.input_value.get("workspace_max_bytes")
        ),
    )


def execute_feature_plan(
    client: BaristaClient,
    run: AppRun,
    *,
    work_root: str | Path = "/work/program-runs",
) -> AppRunResult:
    manifest, owner = _validate_operation(run, "feature-plan")
    replayed = _replay_terminal_result(client, run)
    if replayed is not None:
        return replayed
    started_at = _now()
    root = Path(work_root).expanduser().resolve() / run.name
    root.mkdir(parents=True, exist_ok=False)
    repository = _repository_binding(client, run, root / "base")
    expected_commit = str(run.input_value["approved_commit"])
    if repository.commit != expected_commit:
        raise _invalid(
            "planning repository does not match approved BRD commit",
            "factory.plan_approved_commit",
        )
    brd_relative = Path(str(run.input_value["brd_path"]))
    brd_path = repository.workspace / brd_relative
    if (
        brd_relative.is_absolute()
        or ".." in brd_relative.parts
        or len(brd_relative.parts) < 2
        or brd_path.is_symlink()
        or not brd_path.is_file()
        or brd_path.stat().st_size > MAX_PLAN_BYTES
    ):
        raise _invalid("approved BRD path is invalid", "factory.plan_brd_path")
    brd_bytes = brd_path.read_bytes()
    brd_digest = "sha256:" + hashlib.sha256(brd_bytes).hexdigest()
    if (
        not brd_bytes.startswith(b"# BRD:")
        or brd_digest != run.input_value["brd_digest"]
    ):
        raise _invalid("approved BRD bytes changed", "factory.plan_brd_digest")
    assert_no_high_confidence_secrets(brd_bytes.decode("utf-8"))
    objective = {
        "schema_version": "v1alpha1",
        "program": str(run.input_value["program"]),
        "approved_commit": expected_commit,
        "brd_path": str(run.input_value["brd_path"]),
        "brd_digest": str(run.input_value["brd_digest"]),
    }
    raw_objective = canonical_bytes(objective)
    if len(raw_objective) > OBJECTIVE_LIMIT:
        raise _invalid("planning objective exceeds bound", "factory.plan_objective")
    session = client.ensure_session(
        str(run.input_value["planner_app"]),
        name=f"{run.name}-planner",
        metadata={"role": "factory-feature-planner", "run": run.name},
        idempotency_key=f"{run.content_id()}:planner:worker",
    )
    timeout = int(run.input_value.get("timeout_seconds", 600))
    try:
        if (
            _exec_exit(
                client,
                session.id,
                [
                    "git",
                    "clone",
                    "--no-checkout",
                    "--filter=blob:none",
                    "--",
                    repository.uri,
                    WORKER_PROJECT,
                ],
                timeout=timeout,
                env={"GIT_TERMINAL_PROMPT": "0"},
            )
            != 0
            or _exec_exit(
                client,
                session.id,
                [
                    "git",
                    "-C",
                    WORKER_PROJECT,
                    "checkout",
                    "--detach",
                    repository.commit,
                ],
                timeout=timeout,
                env={"GIT_TERMINAL_PROMPT": "0"},
            )
            != 0
        ):
            raise InvalidRequestError(
                "planner could not acquire exact approved repository",
                code="factory.plan_base",
                error_class="terminal",
            )
        transfer.write_file(client, session.id, PLANNER_OBJECTIVE, raw_objective)
        exit_code = _exec_exit(
            client,
            session.id,
            list(run.input_value["planner"]["command"]),
            timeout=timeout,
            env={
                "BARISTA_PROGRAM_OBJECTIVE_PATH": PLANNER_OBJECTIVE,
                "BARISTA_FEATURE_PLAN_PATH": PLANNER_RESULT,
                "BARISTA_BASE_COMMIT": repository.commit,
            },
            working_dir=WORKER_PROJECT,
        )
        if exit_code != 0:
            raise _terminal("feature planner failed", "factory.plan_failed")
        raw = transfer.read_file_bounded(
            client, session.id, PLANNER_RESULT, max_bytes=MAX_PLAN_BYTES
        )
        plan = FeaturePlan.parse_bytes(raw)
        if (
            plan.program != objective["program"]
            or plan.approved_commit != repository.commit
        ):
            raise _invalid("planner changed program identity", "factory.plan_identity")
        output_path = root / "feature-plan.json"
        output_path.write_bytes(raw)
        client.register_artifact(
            owner,
            name="feature-plan.json",
            digest=plan.content_id(),
            size_bytes=len(raw),
            media_type="application/vnd.barista.feature-plan+json",
            idempotency_key=f"{run.content_id()}:feature-plan",
        )
    except Exception:
        raise
    else:
        try:
            client.delete_session(
                session.id, idempotency_key=f"{run.content_id()}:planner:reap"
            )
        except HostAPIError:
            pass
    document = {
        "schema_version": "v1alpha1",
        "run": run.name,
        "app": run.app,
        "operation": run.operation,
        "state": "succeeded",
        "identity": _identity(run, manifest),
        "bindings": {"workspace": repository.to_result_binding()},
        "outputs": {
            "plan": {
                "kind": "sh.barista.product.feature-plan",
                "uri": output_path.as_uri(),
                "digest": plan.content_id(),
                "media_type": "application/vnd.barista.feature-plan+json",
                "metadata": {"size_bytes": len(raw)},
            }
        },
        "evidence": [
            {
                "kind": "sh.barista.factory.feature-plan",
                "digest": plan.content_id(),
                "metadata": {"features": len(plan.features)},
            }
        ],
        "started_at": started_at,
        "finished_at": _now(),
        "metadata": {"workflow_state": "planned"},
    }
    result = AppRunResult.parse(document)
    register_app_run_result(client, result)
    return result


def execute_program_acceptance(
    client: BaristaClient,
    run: AppRun,
    *,
    work_root: str | Path = "/work/program-runs",
) -> AppRunResult:
    manifest, owner = _validate_operation(run, "program-acceptance")
    replayed = _replay_terminal_result(client, run)
    if replayed is not None:
        return replayed
    started_at = _now()
    root = Path(work_root).expanduser().resolve() / run.name
    root.mkdir(parents=True, exist_ok=False)
    repository = _repository_binding(client, run, root / "assembled")
    expected_commit = str(run.input_value["assembled_commit"])
    if repository.commit != expected_commit:
        raise _invalid(
            "acceptance repository does not match assembled commit",
            "factory.program_acceptance_commit",
        )
    acceptance = run.input_value["acceptance"]
    for relative, content in acceptance.get("files", {}).items():
        target = _safe_acceptance_file(repository.workspace, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    timeout = int(run.input_value.get("timeout_seconds", 600))
    exit_code = _run_local(
        list(acceptance["command"]), cwd=repository.workspace, timeout=timeout
    )
    report = {
        "schema_version": "v1alpha1",
        "program": str(run.input_value["program"]),
        "assembled_commit": repository.commit,
        "features": list(run.input_value["features"]),
        "command_digest": content_id(list(acceptance["command"])),
        "exit_code": exit_code,
        "accepted": exit_code == 0,
    }
    assert_no_high_confidence_secrets(json.dumps(report, sort_keys=True))
    raw = canonical_bytes(report)
    path = root / "program-acceptance.json"
    path.write_bytes(raw)
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    client.register_artifact(
        owner,
        name="program-acceptance.json",
        digest=digest,
        size_bytes=len(raw),
        media_type="application/vnd.barista.program-acceptance+json",
        idempotency_key=f"{run.content_id()}:program-acceptance",
    )
    document: dict[str, Any] = {
        "schema_version": "v1alpha1",
        "run": run.name,
        "app": run.app,
        "operation": run.operation,
        "state": "succeeded" if exit_code == 0 else "failed",
        "identity": _identity(run, manifest),
        "bindings": {"workspace": repository.to_result_binding()},
        "outputs": {
            "result": {
                "kind": "sh.barista.product.program-result",
                "uri": path.as_uri(),
                "digest": digest,
                "media_type": "application/vnd.barista.program-acceptance+json",
                "metadata": {"size_bytes": len(raw)},
            }
        },
        "evidence": [
            {
                "kind": "sh.barista.factory.program-acceptance",
                "digest": digest,
                "metadata": {"exit_code": exit_code},
            }
        ],
        "started_at": started_at,
        "finished_at": _now(),
        "metadata": {"workflow_state": "accepted" if exit_code == 0 else "rejected"},
    }
    if exit_code != 0:
        document["error"] = {
            "code": "factory.program_acceptance_failed",
            "message": "assembled product failed independent acceptance",
        }
    result = AppRunResult.parse(document)
    register_app_run_result(client, result)
    return result
