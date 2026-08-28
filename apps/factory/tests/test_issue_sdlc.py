from __future__ import annotations

import base64
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

from barista_app_factory.issue_sdlc import execute_issue_sdlc
from barista_app_factory.triage import TriageDecisionError
from barista_app_sdk import AppRun
from barista_app_sdk.content import canonical_bytes, content_id

from test_software_change import (
    WorkerClient,
    _forge,
    _patch,
    _repository,
    _result_path,
)


class TriageClient(WorkerClient):
    def __init__(self, patches, decision: bytes):
        super().__init__(patches)
        self.decision = decision
        self.commands: list[tuple[str, list[str]]] = []

    def exec(self, session_id, command, **kwargs):
        self.commands.append((session_id, command))
        if "triage" in session_id:
            if (
                command[:2] == ["sh", "-c"]
                and "sha256sum '/tmp/barista-triage-result.json'" in command[2]
            ):
                with self._lock:
                    self._counter += 1
                    operation_id = f"operation-{self._counter}"
                output = (
                    f"{len(self.decision)}\n{hashlib.sha256(self.decision).hexdigest()}  /tmp/barista-triage-result.json\n"
                ).encode()
                self.operations[operation_id] = (0, output)
                return __import__("barista_app_sdk").ExecHandle(
                    operation_id=operation_id, event_cursor=operation_id
                )
            if command[:1] == ["dd"] and any(
                item == "if=/tmp/barista-triage-result.json" for item in command
            ):
                with self._lock:
                    self._counter += 1
                    operation_id = f"operation-{self._counter}"
                block = int(next(arg[3:] for arg in command if arg.startswith("bs=")))
                index = int(next(arg[5:] for arg in command if arg.startswith("skip=")))
                self.operations[operation_id] = (
                    0,
                    self.decision[index * block : (index + 1) * block],
                )
                return __import__("barista_app_sdk").ExecHandle(
                    operation_id=operation_id, event_cursor=operation_id
                )
        return super().exec(session_id, command, **kwargs)


def _run(repo: Path, acceptance: str, *, attempt: int = 1, answers=None) -> AppRun:
    return AppRun.parse(
        {
            "schema_version": "v1alpha1",
            "name": f"github-repo-issue-7-attempt-{attempt}",
            "app": "factory@0.1.0",
            "operation": "issue-sdlc",
            "input": {
                "media_type": "application/json",
                "value": {
                    "triage_app": "issue-triage",
                    "triage": {"command": ["triage"]},
                    "attempt": attempt,
                    "answers": answers or [],
                    "worker_app": "change-agent",
                    "tasks": [{"id": "a", "command": ["worker", "a"]}],
                    "acceptance": {
                        "command": [sys.executable, "acceptance.py"],
                        "files": {"acceptance.py": acceptance},
                    },
                    "branch": "barista/issue-7",
                    "title": "Fix value",
                    "body": "Verified for review.",
                },
            },
            "bindings": {
                "workspace": {
                    "kind": "sh.barista.git.repository",
                    "uri": repo.as_uri(),
                    "ref": "main",
                },
                "objective": {
                    "kind": "com.github.issue",
                    "uri": "https://github.com/acme/project/issues/7",
                },
            },
            "deliveries": {
                "change": {
                    "kind": "com.github.draft-pull-request",
                    "target": repo.as_uri(),
                    "options": {
                        "base_ref": "main",
                        "head_branch": "barista/issue-7",
                        "executor": "runner",
                    },
                },
                "question": {
                    "kind": "com.github.issue-comment",
                    "target": "https://github.com/acme/project/issues/7",
                    "options": {"executor": "runner"},
                },
            },
        }
    )


def test_needs_input_stops_before_implementation_and_returns_verified_question(
    tmp_path, monkeypatch
):
    repo, commit, acceptance = _repository(tmp_path)
    decision = canonical_bytes(
        {
            "schema_version": "v1alpha1",
            "state": "needs_input",
            "questions": ["Must the old file format remain readable?"],
        }
    )
    client = TriageClient({}, decision)
    _result_path(tmp_path, monkeypatch)
    run = _run(repo, acceptance)

    result = execute_issue_sdlc(
        client,
        run,
        forge=_forge(repo, commit),
        work_root=tmp_path / "triage-runs",
    ).to_document()

    assert result["state"] == "succeeded"
    assert result["metadata"]["workflow_state"] == "needs_input"
    assert not any(command[:1] == ["worker"] for _, command in client.commands)
    output = result["outputs"]["question"]
    question = json.loads(Path(output["uri"].removeprefix("file://")).read_bytes())
    assert question == {
        "schema_version": "v1alpha1",
        "kind": "clarification",
        "issue": "https://github.com/acme/project/issues/7",
        "attempt": 1,
        "questions": ["Must the old file format remain readable?"],
    }
    assert output["digest"] == content_id(question)
    assert result["metadata"]["pending_deliveries"]["question"] == {
        "kind": "com.github.issue-comment",
        "target": question["issue"],
        "request_digest": content_id(run.deliveries["question"].to_document()),
    }
    assert client.deleted == [run.name + "-triage"]


def test_refused_triage_stops_without_question_or_implementation(tmp_path, monkeypatch):
    repo, commit, acceptance = _repository(tmp_path)
    decision = canonical_bytes(
        {
            "schema_version": "v1alpha1",
            "state": "refused",
            "reason_code": "objective_refused",
            "message": "The objective is outside policy.",
        }
    )
    client = TriageClient({}, decision)
    _result_path(tmp_path, monkeypatch)

    result = execute_issue_sdlc(
        client,
        _run(repo, acceptance),
        forge=_forge(repo, commit),
        work_root=tmp_path / "triage-runs",
    ).to_document()

    assert result["state"] == "succeeded"
    assert result["metadata"]["workflow_state"] == "refused"
    assert result["metadata"]["pending_deliveries"] == {}
    assert result["outputs"] == {}
    assert not any(command[:1] == ["worker"] for _, command in client.commands)


def test_ready_triage_feeds_existing_independent_software_change(tmp_path, monkeypatch):
    repo, commit, acceptance = _repository(tmp_path)
    patch = _patch(repo, tmp_path / "worker", {"a.txt": "1\n", "b.txt": "1\n"})
    decision = canonical_bytes(
        {
            "schema_version": "v1alpha1",
            "state": "ready",
            "summary": "Set both values to one.",
            "acceptance_criteria": ["Both files contain one."],
        }
    )
    client = TriageClient({"a": patch}, decision)
    _result_path(tmp_path, monkeypatch)

    result = execute_issue_sdlc(
        client,
        _run(repo, acceptance, attempt=2, answers=[{"comment_id": 9, "body": "Yes."}]),
        forge=_forge(repo, commit),
        work_root=tmp_path / "triage-runs",
    ).to_document()

    assert result["state"] == "succeeded", result.get("error")
    assert result["metadata"]["workflow_state"] == "verified_for_review"
    assert result["operation"] == "issue-sdlc"
    assert result["outputs"]["patch"]["digest"].startswith("sha256:")
    assert result["metadata"]["pending_deliveries"]["change"]["target"] == repo.as_uri()
    assert any(command[:1] == ["worker"] for _, command in client.commands)
    objective_write = next(
        command[2]
        for session, command in client.commands
        if session.endswith("-a")
        and command[:2] == ["sh", "-c"]
        and "/tmp/barista-objective.txt" in command[2]
    )
    match = re.search(r"printf %s ([A-Za-z0-9+/=]+)", objective_write)
    assert match is not None
    worker_objective = json.loads(base64.b64decode(match.group(1)))
    assert worker_objective["factory_context"]["triage"]["state"] == "ready"
    assert worker_objective["factory_context"]["answers"][0]["comment_id"] == 9
    assert len([item for item in result["evidence"] if "triage" in item["kind"]]) == 1


def test_independent_acceptance_failure_returns_only_sanitized_failure_question(
    tmp_path, monkeypatch
):
    repo, commit, acceptance = _repository(tmp_path)
    patch = _patch(repo, tmp_path / "worker-failing-check", {"a.txt": "1\n"})
    decision = canonical_bytes(
        {
            "schema_version": "v1alpha1",
            "state": "ready",
            "summary": "Set one value.",
            "acceptance_criteria": ["Repository acceptance decides."],
        }
    )
    client = TriageClient({"a": patch}, decision)
    _result_path(tmp_path, monkeypatch)
    run = _run(repo, acceptance)

    result = execute_issue_sdlc(
        client,
        run,
        forge=_forge(repo, commit),
        work_root=tmp_path / "triage-runs",
    ).to_document()

    assert result["state"] == "succeeded"
    assert result["metadata"]["workflow_state"] == "needs_input"
    assert result["metadata"]["recoverable_failure"] == {
        "code": "factory.acceptance_failed"
    }
    assert set(result["outputs"]) == {"question"}
    assert set(result["metadata"]["pending_deliveries"]) == {"question"}
    question = json.loads(
        Path(result["outputs"]["question"]["uri"].removeprefix("file://")).read_bytes()
    )
    assert question["kind"] == "failure"
    assert "stderr" not in json.dumps(question).casefold()


def test_malformed_triage_is_integrity_failure_with_no_delivery(tmp_path, monkeypatch):
    repo, commit, acceptance = _repository(tmp_path)
    client = TriageClient({}, b'{"state":"needs_input","questions":[]}')
    result_path = _result_path(tmp_path, monkeypatch)
    run = _run(repo, acceptance)

    with pytest.raises(TriageDecisionError):
        execute_issue_sdlc(
            client,
            run,
            forge=_forge(repo, commit),
            work_root=tmp_path / "triage-runs",
        )

    assert not result_path.exists()
    assert client.deleted == []
    assert not any(
        name == "issue-question.json"
        for _, artifact in client.artifacts
        for name in [artifact.name]
    )
