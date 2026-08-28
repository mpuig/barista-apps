from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import pytest

from barista_app_sdk import (
    APP_RUN_RESULT_ARTIFACT,
    APP_RUN_RESULT_MEDIA_TYPE,
    AppRun,
    AppRunResult,
    Artifact,
    Event,
    ExecHandle,
    Operation,
    RunOperation,
    Session,
    canonical_bytes,
    collect_app_run_result,
    errors,
    register_app_run_result,
    wait_app_run,
)


def _run() -> AppRun:
    return AppRun.parse(
        {
            "schema_version": "v1alpha1",
            "name": "review-website",
            "app": "reviewer@1.0.0",
            "operation": "review",
            "input": {"media_type": "application/json", "value": {"instructions": "review"}},
        }
    )


def _result(run: AppRun, *, state: str = "succeeded") -> bytes:
    return canonical_bytes(
        {
            "schema_version": "v1alpha1",
            "run": run.name,
            "app": run.app,
            "operation": run.operation,
            "state": state,
            "identity": {
                "name": "reviewer",
                "version": "1.0.0",
                "workload_digest": "sha256:" + "12" * 32,
            },
            "bindings": {},
            "outputs": {
                "report": {
                    "kind": "sh.barista.artifact",
                    "uri": "file:///tmp/report.json",
                }
            },
        }
    )


class _Client:
    def __init__(self, raw: bytes, *, digest: str | None = None, state: str = "running"):
        self.raw = raw
        self.state = state
        self.deleted = False
        self.output_must_exist: Path | None = None
        self.registered = None
        self.artifact = Artifact(
            id="artifact-1",
            name=APP_RUN_RESULT_ARTIFACT,
            digest=digest or "sha256:" + hashlib.sha256(raw).hexdigest(),
            size_bytes=len(raw),
            media_type=APP_RUN_RESULT_MEDIA_TYPE,
            created_at="2026-08-28T00:00:00Z",
        )

    def register_artifact(self, session_id: str, **fields):
        self.registered = (session_id, fields)
        return Artifact(
            id="registered-1",
            name=fields["name"],
            digest=fields["digest"],
            size_bytes=fields["size_bytes"],
            media_type=fields["media_type"],
            created_at="2026-08-28T00:00:00Z",
        )

    def list_artifacts(self, session_id: str):
        return [self.artifact]

    def get_session(self, session_id: str):
        return Session(
            id=session_id,
            app="reviewer",
            state=self.state,
            created_at="2026-08-28T00:00:00Z",
        )

    def exec(self, session_id: str, command: list[str], *, idempotency_key: str):
        assert command == ["cat", "/tmp/barista/app-run-result.json"]
        return ExecHandle(operation_id="operation-1", event_cursor="10")

    def wait_operation(self, operation_id: str, *, timeout: float):
        return Operation(id=operation_id, kind="exec", done=True, result={"exit_code": 0})

    def events(self, session_id: str, *, cursor: str, max_events: int):
        # operation_id is optional by contract. Collection must still accept the
        # output selected by the exec handle's exclusive cursor.
        yield Event(
            cursor="11",
            type="exec.stdout",
            session_id=session_id,
            time="2026-08-28T00:00:01Z",
            data={"chunk": base64.b64encode(self.raw).decode()},
        )
        yield Event(
            cursor="12",
            type="exec.exit",
            session_id=session_id,
            time="2026-08-28T00:00:02Z",
            data={"exit_code": 0},
        )

    def delete_session(self, session_id: str, *, idempotency_key: str):
        if self.output_must_exist is not None:
            assert self.output_must_exist.read_bytes() == self.raw
        self.deleted = True
        return Operation(id="delete-1", kind="delete", done=False)


def test_app_registers_result_on_provider_injected_owning_scope(tmp_path, monkeypatch):
    import barista_app_sdk.lifecycle as lifecycle

    run = _run()
    result = AppRunResult.parse(__import__("json").loads(_result(run)))
    client = _Client(_result(run))
    result_path = tmp_path / "app-run-result.json"
    monkeypatch.setattr(lifecycle, "APP_RUN_RESULT_PATH", str(result_path))
    monkeypatch.setenv("BARISTA_APP_SESSION_ID", "owner-1")

    artifact = register_app_run_result(client, result)

    assert result_path.read_bytes() == result.canonical_bytes()
    assert artifact.name == APP_RUN_RESULT_ARTIFACT
    assert client.registered[0] == "owner-1"
    assert client.registered[1]["digest"] == result.content_id()


def test_register_refuses_missing_owning_scope(tmp_path, monkeypatch):
    import barista_app_sdk.lifecycle as lifecycle

    run = _run()
    result = AppRunResult.parse(__import__("json").loads(_result(run)))
    client = _Client(_result(run))
    monkeypatch.setattr(lifecycle, "APP_RUN_RESULT_PATH", str(tmp_path / "result.json"))
    monkeypatch.delenv("BARISTA_APP_SESSION_ID", raising=False)

    with pytest.raises(errors.InvalidRequestError) as caught:
        register_app_run_result(client, result)

    assert caught.value.code == "app_run.session_id_missing"
    assert client.registered is None


def test_collect_verifies_persists_then_cleans_up(tmp_path):
    run = _run()
    raw = _result(run)
    client = _Client(raw)
    output = tmp_path / "nested" / "result.json"
    client.output_must_exist = output

    collected = collect_app_run_result(
        client, run, "session-1", output=output, cleanup=True, timeout=1, poll=0.001
    )

    assert collected.result.to_document()["state"] == "succeeded"
    assert collected.bytes == raw
    assert collected.output_path == output
    assert collected.session_deleted is True
    assert client.deleted is True


def test_digest_mismatch_preserves_owning_session(tmp_path):
    run = _run()
    client = _Client(_result(run), digest="sha256:" + "00" * 32)

    with pytest.raises(errors.ResultIntegrityError) as caught:
        collect_app_run_result(
            client, run, "session-1", output=tmp_path / "result.json", cleanup=True,
            timeout=1, poll=0.001,
        )

    assert caught.value.code == "app_run.result_digest_mismatch"
    assert client.deleted is False
    assert not (tmp_path / "result.json").exists()


def test_result_for_another_run_is_refused_and_session_preserved():
    run = _run()
    document = __import__("json").loads(_result(run))
    document["run"] = "different-run"
    client = _Client(canonical_bytes(document))

    with pytest.raises(errors.ResultIntegrityError) as caught:
        collect_app_run_result(client, run, "session-1", cleanup=True, timeout=1, poll=0.001)

    assert caught.value.code == "app_run.result_identity_mismatch"
    assert client.deleted is False


def test_resolved_workload_identity_mismatch_is_refused():
    run = _run()
    client = _Client(_result(run))

    with pytest.raises(errors.ResultIntegrityError) as caught:
        collect_app_run_result(
            client,
            run,
            "session-1",
            timeout=1,
            poll=0.001,
            expected_identity={
                "name": "reviewer",
                "version": "1.0.0",
                "workload_digest": "sha256:" + "99" * 32,
            },
        )

    assert caught.value.code == "app_run.result_identity_mismatch"
    assert "identity.workload_digest" in caught.value.details["mismatches"]
    assert client.deleted is False


def test_noncanonical_result_is_refused():
    run = _run()
    raw = _result(run).replace(b'"app":"reviewer@1.0.0",', b'"app": "reviewer@1.0.0",')
    client = _Client(raw)

    with pytest.raises(errors.ResultIntegrityError) as caught:
        collect_app_run_result(client, run, "session-1", timeout=1, poll=0.001)

    assert caught.value.code == "app_run.result_not_canonical"
    assert client.deleted is False


def test_service_lifecycle_returns_when_session_is_running():
    run = _run()
    client = _Client(_result(run), state="running")
    operation = RunOperation(
        name="serve",
        lifecycle="service",
        input_media_type="application/json",
        input_schema=None,
        bindings={},
        outputs={},
        deliveries={},
    )
    session = client.get_session("session-1")

    observed = wait_app_run(client, run, session, operation, timeout=1, poll=0.001)

    assert observed.id == "session-1"
    assert client.deleted is False


def test_invalid_result_schema_is_an_integrity_error():
    run = _run()
    client = _Client(canonical_bytes({"schema_version": "v1alpha1"}))

    with pytest.raises(errors.ResultIntegrityError) as caught:
        collect_app_run_result(client, run, "session-1", timeout=1, poll=0.001)

    assert caught.value.code == "app_run.result_invalid"
    assert client.deleted is False
