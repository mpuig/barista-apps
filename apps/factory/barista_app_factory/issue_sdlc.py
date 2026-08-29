"""Bounded triage-first issue SDLC operation."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from barista_app_sdk import (
    AppRun,
    AppRunResult,
    BaristaClient,
    ForgeAdapter,
    materialize_git_repository,
    register_app_run_result,
    resolve_issue_objective,
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
    execute_software_change,
    load_manifest,
)
from .triage import MAX_DECISION_BYTES, TriageDecision

OPERATIONS = {"issue-sdlc", "product-brief"}
TRIAGE_OBJECTIVE = "/tmp/barista-triage-objective.json"
TRIAGE_RESULT = "/tmp/barista-triage-result.json"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _software_change_run(run: AppRun) -> AppRun:
    value = run.input_value
    change_value = {
        key: value[key]
        for key in (
            "worker_app",
            "tasks",
            "acceptance",
            "concurrency",
            "timeout_seconds",
            "workspace_max_bytes",
            "patch_max_bytes",
            "branch",
            "commit_message",
            "title",
            "body",
        )
        if key in value
    }
    document = run.to_document()
    document["operation"] = "software-change"
    document["input"] = {"media_type": "application/json", "value": change_value}
    document["deliveries"] = (
        {"change": run.deliveries["change"].to_document()}
        if "change" in run.deliveries
        else {}
    )
    return AppRun.parse(document)


def _triage_context(issue, run: AppRun, commit: str) -> bytes:
    answers = [dict(answer) for answer in run.input_value.get("answers", [])]
    document = {
        "schema_version": "v1alpha1",
        "issue": {
            "kind": issue.kind,
            "uri": issue.uri,
            "repository_uri": issue.repository_uri,
            "number": issue.number,
            "title": issue.title,
            "body": issue.body,
            "revision": issue.revision,
        },
        "attempt": int(run.input_value["attempt"]),
        "base_commit": commit,
        "answers": answers,
    }
    raw = canonical_bytes(document)
    if len(raw) > OBJECTIVE_LIMIT:
        raise _invalid(
            "triage objective exceeds the supported bound", "factory.triage_objective"
        )
    return raw


def _run_triage(
    client: BaristaClient,
    *,
    run: AppRun,
    repository,
    objective: bytes,
    owner: str,
) -> tuple[TriageDecision, str]:
    session = client.ensure_session(
        str(run.input_value["triage_app"]),
        name=f"{run.name}-triage",
        metadata={"role": "factory-triage-worker", "run": run.name},
        idempotency_key=f"{run.content_id()}:triage:worker",
    )
    timeout = int(run.input_value.get("timeout_seconds", 600))
    clone_env = {"GIT_TERMINAL_PROMPT": "0"}
    if repository.lfs == "pointer-files":
        clone_env["GIT_LFS_SKIP_SMUDGE"] = "1"
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
            env=clone_env,
        )
        != 0
        or _exec_exit(
            client,
            session.id,
            ["git", "-C", WORKER_PROJECT, "checkout", "--detach", repository.commit],
            timeout=timeout,
            env=clone_env,
        )
        != 0
    ):
        raise InvalidRequestError(
            "triage worker could not acquire the exact repository base",
            code="factory.triage_base",
            error_class="terminal",
        )
    transfer.write_file(client, session.id, TRIAGE_OBJECTIVE, objective)
    exit_code = _exec_exit(
        client,
        session.id,
        list(run.input_value["triage"]["command"]),
        timeout=timeout,
        env={
            "BARISTA_TRIAGE_OBJECTIVE_PATH": TRIAGE_OBJECTIVE,
            "BARISTA_TRIAGE_RESULT_PATH": TRIAGE_RESULT,
            "BARISTA_BASE_COMMIT": repository.commit,
        },
        working_dir=WORKER_PROJECT,
    )
    if exit_code != 0:
        raise InvalidRequestError(
            "triage worker failed",
            code="factory.triage_failed",
            error_class="terminal",
        )
    raw = transfer.read_file_bounded(
        client, session.id, TRIAGE_RESULT, max_bytes=MAX_DECISION_BYTES
    )
    decision = TriageDecision.parse_bytes(raw)
    receipt = {
        "phase": "issue-triage",
        "worker": session.id,
        "decision": decision.state,
        "decision_digest": decision.content_id(),
    }
    blob = canonical_bytes(receipt)
    client.register_artifact(
        owner,
        name="issue-triage-receipt.json",
        digest=content_id(receipt),
        size_bytes=len(blob),
        media_type="application/vnd.barista.factory.triage-receipt+json",
        idempotency_key=f"{run.content_id()}:triage:receipt",
    )
    try:
        client.delete_session(
            session.id, idempotency_key=f"{run.content_id()}:triage:reap"
        )
    except HostAPIError:
        pass
    return decision, session.id


def _write_question(
    client: BaristaClient,
    *,
    owner: str,
    run: AppRun,
    root: Path,
    issue_uri: str,
    kind: str,
    questions: list[str],
) -> tuple[dict, str]:
    question = {
        "schema_version": "v1alpha1",
        "kind": kind,
        "issue": issue_uri,
        "attempt": int(run.input_value["attempt"]),
        "questions": questions,
    }
    assert_no_high_confidence_secrets(json.dumps(question, ensure_ascii=False))
    raw = canonical_bytes(question)
    path = root / "question.json"
    path.write_bytes(raw)
    digest = content_id(question)
    output = {
        "kind": "com.github.issue-question",
        "uri": path.as_uri(),
        "digest": digest,
        "media_type": "application/json",
        "metadata": {"size_bytes": len(raw)},
    }
    client.register_artifact(
        owner,
        name="issue-question.json",
        digest=digest,
        size_bytes=len(raw),
        media_type="application/json",
        idempotency_key=f"{run.content_id()}:question:{kind}",
    )
    return output, digest


def execute_issue_sdlc(
    client: BaristaClient,
    run: AppRun,
    *,
    forge: ForgeAdapter | None,
    work_root: str | Path = "/work/triage-runs",
) -> AppRunResult:
    """Triage one immutable attempt, then stop or run software-change."""
    manifest = load_manifest()
    operation = validate_run(run, manifest)
    selected_name, separator, selected_version = run.app.rpartition("@")
    if (
        operation.name not in OPERATIONS
        or not separator
        or not selected_name
        or selected_version != manifest["version"]
    ):
        raise _invalid(
            "App Run does not select Factory issue-sdlc", "factory.run_identity"
        )
    owner = os.environ.get("BARISTA_APP_SESSION_ID")
    if not owner:
        raise _invalid(
            "provider did not inject BARISTA_APP_SESSION_ID", "factory.owner_missing"
        )
    replayed = _replay_terminal_result(client, run)
    if replayed is not None:
        return replayed
    if forge is None:
        raise _invalid(
            "issue-sdlc requires an objective forge adapter", "factory.forge_missing"
        )
    if run.input_value["triage_app"] == run.input_value["worker_app"]:
        raise _invalid(
            "triage and implementation require separate app identities",
            "factory.worker_authority",
        )

    workspace_binding = run.bindings["workspace"]
    objective_binding = run.bindings["objective"]
    if workspace_binding.credential is not None:
        raise _invalid(
            "repository credential alias was not materialized for Factory",
            "factory.repository_credential_unavailable",
        )
    for name in ("change", "question"):
        delivery = run.deliveries.get(name)
        if delivery is not None and delivery.target not in {
            workspace_binding.uri,
            objective_binding.uri,
        }:
            raise _invalid(
                "delivery target is outside bound scope", "factory.delivery_scope"
            )
    question_delivery = run.deliveries.get("question")
    if question_delivery is None or question_delivery.target != objective_binding.uri:
        raise _invalid(
            "issue-sdlc requires issue-scoped question delivery",
            "factory.question_delivery",
        )

    started_at = _now()
    root = Path(work_root).expanduser().resolve() / run.name
    bindings: dict[str, dict] = {}
    triage_evidence: list[dict] = []
    try:
        root.mkdir(parents=True, exist_ok=False)
        repository = materialize_git_repository(
            workspace_binding,
            root / "base",
            max_bytes=_effective_workspace_limit(
                client, run.input_value.get("workspace_max_bytes")
            ),
        )
        issue = resolve_issue_objective(
            objective_binding, forge, max_bytes=OBJECTIVE_LIMIT
        )
        if issue.repository_uri != repository.uri:
            raise _invalid(
                "issue objective belongs to a different repository",
                "factory.objective_repository_scope",
            )
        bindings = {
            "workspace": repository.to_result_binding(),
            "objective": issue.to_result_binding(),
        }
        decision, triage_worker = _run_triage(
            client,
            run=run,
            repository=repository,
            objective=_triage_context(issue, run, repository.commit),
            owner=owner,
        )
        triage_evidence.append(
            {
                "kind": "sh.barista.factory.triage-decision",
                "digest": decision.content_id(),
                "metadata": {"state": decision.state, "worker": triage_worker},
            }
        )
    except Exception:
        # A malformed, secret-bearing, unavailable, or otherwise unverifiable
        # triage attempt is an integrity failure. It publishes no question.
        raise

    if decision.state == "ready":
        nested = execute_software_change(
            client,
            _software_change_run(run),
            forge=forge,
            work_root=Path(work_root).expanduser().resolve().parent / "app-runs",
            register_result=False,
            objective_context={
                "triage": decision.to_document(),
                "answers": [
                    dict(answer) for answer in run.input_value.get("answers", [])
                ],
            },
        ).to_document()
        nested["operation"] = run.operation
        nested["evidence"] = triage_evidence + list(nested.get("evidence", []))
        metadata = dict(nested.get("metadata", {}))
        failure_code = str((nested.get("error") or {}).get("code", ""))
        recoverable_questions = {
            "factory.worker_failed": "The implementation worker did not complete. What additional constraint should guide a fresh attempt?",
            "factory.acceptance_failed": "Independent acceptance failed. What expected behavior or compatibility constraint should guide a fresh attempt?",
        }
        if nested["state"] != "succeeded" and failure_code in recoverable_questions:
            question_output, _ = _write_question(
                client,
                owner=owner,
                run=run,
                root=root,
                issue_uri=issue.uri,
                kind="failure",
                questions=[recoverable_questions[failure_code]],
            )
            nested["state"] = "succeeded"
            nested.pop("error", None)
            nested["outputs"] = {"question": question_output}
            metadata["workflow_state"] = "needs_input"
            metadata["recoverable_failure"] = {"code": failure_code}
            metadata["pending_deliveries"] = {
                "question": {
                    "kind": question_delivery.kind,
                    "target": question_delivery.target,
                    "request_digest": content_id(question_delivery.to_document()),
                }
            }
        else:
            metadata["workflow_state"] = (
                "verified_for_review" if nested["state"] == "succeeded" else "failed"
            )
        metadata["triage"] = decision.to_document()
        nested["metadata"] = metadata
        result = AppRunResult.parse(nested)
        register_app_run_result(client, result)
        return result

    outputs: dict[str, dict] = {}
    pending: dict[str, dict] = {}
    metadata: dict[str, Any] = {
        "workflow_state": decision.state,
        "triage": decision.to_document(),
        "pending_deliveries": pending,
    }
    if decision.state == "needs_input":
        outputs["question"], _ = _write_question(
            client,
            owner=owner,
            run=run,
            root=root,
            issue_uri=issue.uri,
            kind="clarification",
            questions=list(decision.questions),
        )
        pending["question"] = {
            "kind": question_delivery.kind,
            "target": question_delivery.target,
            "request_digest": content_id(question_delivery.to_document()),
        }
    else:
        metadata["refusal"] = {
            "reason_code": decision.reason_code,
            "message": decision.message,
        }

    document = {
        "schema_version": "v1alpha1",
        "run": run.name,
        "app": run.app,
        "operation": run.operation,
        "state": "succeeded",
        "identity": _identity(run, manifest),
        "bindings": bindings,
        "outputs": outputs,
        "evidence": triage_evidence,
        "started_at": started_at,
        "finished_at": _now(),
        "metadata": metadata,
    }
    result = AppRunResult.parse(document)
    register_app_run_result(client, result)
    return result
