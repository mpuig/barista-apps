"""Lifecycle observation and verified App Run result collection.

The Host API remains the scheduler.  This module interprets an operation's
manifest-declared lifecycle over sessions, artifacts, exec, and delete without
inventing a provider-side Run resource.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Optional, Union

from . import errors
from .models import Artifact, Session
from .runs import (
    APP_RUN_RESULT_MEDIA_TYPE,
    APP_SESSION_ID_ENV,
    AppRun,
    AppRunResult,
    RunOperation,
)

if TYPE_CHECKING:
    from .client import BaristaClient

APP_RUN_RESULT_ARTIFACT = "app-run-result.json"
APP_RUN_RESULT_PATH = "/tmp/barista/app-run-result.json"
DEFAULT_MAX_RESULT_BYTES = 4 * 1024 * 1024
_TERMINAL_RESULT_STATES = frozenset({"succeeded", "failed", "cancelled", "lost_authority"})
_SESSION_FAILURE_STATES = frozenset({"error", "failed", "destroyed", "stopped"})


@dataclass(frozen=True)
class CollectedAppRun:
    """A digest-verified terminal result and where it was persisted."""

    session_id: str
    artifact: Artifact
    result: AppRunResult
    bytes: bytes
    output_path: Optional[Path] = None
    session_deleted: bool = False


def register_app_run_result(
    client: "BaristaClient",
    result: AppRunResult,
    *,
    session_id: str | None = None,
) -> Artifact:
    """Write and register a canonical result from inside its owning workload.

    The provider-reserved session id is used by default. The bytes are written
    before registration, making artifact visibility the terminal rendezvous.
    """

    owner = session_id or os.environ.get(APP_SESSION_ID_ENV)
    if not owner:
        raise errors.InvalidRequestError(
            f"owning session is unavailable; {APP_SESSION_ID_ENV} was not injected",
            code="app_run.session_id_missing",
            error_class="invalid_request",
        )
    raw = result.canonical_bytes()
    path = Path(APP_RUN_RESULT_PATH)
    _atomic_write(path, raw)
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    return client.register_artifact(
        owner,
        name=APP_RUN_RESULT_ARTIFACT,
        digest=digest,
        size_bytes=len(raw),
        media_type=APP_RUN_RESULT_MEDIA_TYPE,
        idempotency_key=f"app-run-result-{result.content_id().split(':', 1)[1]}",
    )


def wait_app_run(
    client: "BaristaClient",
    run: AppRun,
    session: Session,
    operation: RunOperation,
    *,
    output: str | Path | None = None,
    cleanup: bool = False,
    timeout: float = 600.0,
    poll: float = 0.5,
    max_result_bytes: int = DEFAULT_MAX_RESULT_BYTES,
    expected_identity: Mapping[str, Any] | None = None,
) -> Union[Session, CollectedAppRun]:
    """Observe the declared lifecycle and return only its valid completion.

    Service and interactive operations return once their owning session is
    running. Job and coordinator operations wait for the registered canonical
    result, verify it, optionally persist it, and only then perform cleanup.
    Any observation or collection failure leaves the owning session intact.
    """

    if operation.lifecycle in {"service", "interactive"}:
        return _wait_until_running(client, session.id, timeout=timeout, poll=poll)
    if operation.lifecycle not in {"job", "coordinator"}:
        raise errors.InvalidRequestError(
            f"unsupported App Run lifecycle {operation.lifecycle!r}",
            code="app_run.lifecycle_invalid",
            error_class="invalid_request",
        )
    return collect_app_run_result(
        client,
        run,
        session.id,
        output=output,
        cleanup=cleanup,
        timeout=timeout,
        poll=poll,
        max_result_bytes=max_result_bytes,
        expected_identity=expected_identity,
    )


def collect_app_run_result(
    client: "BaristaClient",
    run: AppRun,
    session_id: str,
    *,
    output: str | Path | None = None,
    cleanup: bool = False,
    timeout: float = 600.0,
    poll: float = 0.5,
    max_result_bytes: int = DEFAULT_MAX_RESULT_BYTES,
    expected_identity: Mapping[str, Any] | None = None,
) -> CollectedAppRun:
    """Collect, validate, and persist a terminal result before optional cleanup."""

    if timeout <= 0 or poll <= 0:
        raise ValueError("timeout and poll must be positive")
    if max_result_bytes <= 0:
        raise ValueError("max_result_bytes must be positive")

    artifact = _wait_for_result_artifact(client, session_id, timeout=timeout, poll=poll)
    if artifact.media_type != APP_RUN_RESULT_MEDIA_TYPE:
        raise errors.ResultIntegrityError(
            f"result artifact has media type {artifact.media_type!r}, expected {APP_RUN_RESULT_MEDIA_TYPE!r}",
            code="app_run.result_media_type",
            details={"artifact_id": artifact.id},
        )
    if artifact.size_bytes > max_result_bytes:
        raise errors.ResultIntegrityError(
            f"result artifact is {artifact.size_bytes} bytes, above the {max_result_bytes}-byte limit",
            code="app_run.result_too_large",
            details={"artifact_id": artifact.id, "size_bytes": artifact.size_bytes},
        )

    raw = _read_result_bytes(client, session_id, max_events=max(100, artifact.size_bytes // 1024 + 20))
    if len(raw) != artifact.size_bytes:
        raise errors.ResultIntegrityError(
            "collected result size does not match its registration",
            code="app_run.result_size_mismatch",
            details={"expected": artifact.size_bytes, "actual": len(raw)},
        )
    actual_digest = _digest(raw, artifact.digest)
    if actual_digest != artifact.digest:
        raise errors.ResultIntegrityError(
            "collected result digest does not match its registration",
            code="app_run.result_digest_mismatch",
            details={"expected": artifact.digest, "actual": actual_digest},
        )

    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise errors.ResultIntegrityError(
            "collected result is not UTF-8 JSON",
            code="app_run.result_invalid_json",
        ) from exc
    try:
        result = AppRunResult.parse(document)
    except errors.InvalidRequestError as exc:
        raise errors.ResultIntegrityError(
            f"collected result violates the App Run Result contract: {exc}",
            code="app_run.result_invalid",
            details=exc.details,
        ) from exc

    if result.canonical_bytes() != raw:
        raise errors.ResultIntegrityError(
            "collected result is valid JSON but not canonical App Run Result bytes",
            code="app_run.result_not_canonical",
        )
    _verify_result_identity(run, result, expected_identity=expected_identity)

    output_path = Path(output).expanduser() if output is not None else None
    if output_path is not None:
        _atomic_write(output_path, raw)

    deleted = False
    if cleanup:
        deletion = client.delete_session(
            session_id, idempotency_key=f"app-run-{run.content_id().split(':', 1)[1]}-cleanup"
        )
        client.wait_operation(deletion.id, timeout=min(timeout, 120.0))
        deleted = True

    return CollectedAppRun(
        session_id=session_id,
        artifact=artifact,
        result=result,
        bytes=raw,
        output_path=output_path,
        session_deleted=deleted,
    )


def _wait_until_running(client: "BaristaClient", session_id: str, *, timeout: float, poll: float) -> Session:
    deadline = time.monotonic() + timeout
    while True:
        session = client.get_session(session_id)
        if session.state == "running":
            return session
        if session.state in _SESSION_FAILURE_STATES:
            raise errors.TerminalError(
                f"owning session {session_id} entered {session.state} before readiness",
                code="app_run.session_terminal",
                details={"session_id": session_id, "state": session.state},
                error_class="terminal",
            )
        if time.monotonic() >= deadline:
            raise errors.ResultCollectionError(
                f"owning session {session_id} did not become ready within {timeout}s",
                code="app_run.readiness_timeout",
                details={"session_id": session_id},
            )
        time.sleep(min(poll, max(0.0, deadline - time.monotonic())))


def _wait_for_result_artifact(
    client: "BaristaClient", session_id: str, *, timeout: float, poll: float
) -> Artifact:
    deadline = time.monotonic() + timeout
    while True:
        matches = [a for a in client.list_artifacts(session_id) if a.name == APP_RUN_RESULT_ARTIFACT]
        if len(matches) > 1:
            raise errors.ResultIntegrityError(
                "owning session registered more than one canonical result artifact",
                code="app_run.result_ambiguous",
                details={"session_id": session_id, "artifact_ids": [a.id for a in matches]},
            )
        if matches:
            return matches[0]
        session = client.get_session(session_id)
        if session.state in _SESSION_FAILURE_STATES:
            raise errors.TerminalError(
                f"owning session {session_id} entered {session.state} without a registered result",
                code="app_run.result_missing",
                details={"session_id": session_id, "state": session.state},
                error_class="terminal",
            )
        if time.monotonic() >= deadline:
            raise errors.ResultCollectionError(
                f"owning session {session_id} did not register a result within {timeout}s",
                code="app_run.result_timeout",
                details={"session_id": session_id},
            )
        time.sleep(min(poll, max(0.0, deadline - time.monotonic())))


def _read_result_bytes(client: "BaristaClient", session_id: str, *, max_events: int) -> bytes:
    handle = client.exec(
        session_id,
        ["cat", APP_RUN_RESULT_PATH],
        idempotency_key=f"app-run-result-read-{session_id}",
    )
    operation = client.wait_operation(handle.operation_id, timeout=120.0)
    stdout = bytearray()
    saw_exit = False
    for event in client.events(session_id, cursor=handle.event_cursor, max_events=max_events):
        # operation_id is optional in the event contract. An explicit different
        # id is unrelated; an absent id still belongs to this exclusive cursor.
        if event.operation_id is not None and event.operation_id != handle.operation_id:
            continue
        if event.type == "exec.stdout":
            import base64

            stdout.extend(base64.b64decode(event.data.get("chunk", ""), validate=True))
        elif event.type == "exec.exit":
            saw_exit = True
            break
    exit_code = (operation.result or {}).get("exit_code")
    if exit_code != 0 or not saw_exit:
        raise errors.ResultCollectionError(
            "could not read the registered result from the owning session",
            code="app_run.result_unreadable",
            details={"session_id": session_id, "exit_code": exit_code},
        )
    return bytes(stdout)


def _digest(raw: bytes, registered: str) -> str:
    algorithm, _, _ = registered.partition(":")
    if algorithm not in {"sha256", "sha512"}:
        raise errors.ResultIntegrityError(
            f"unsupported result digest algorithm {algorithm!r}",
            code="app_run.result_digest_algorithm",
        )
    return f"{algorithm}:{hashlib.new(algorithm, raw).hexdigest()}"


def _verify_result_identity(
    run: AppRun,
    result: AppRunResult,
    *,
    expected_identity: Mapping[str, Any] | None,
) -> None:
    document = result.to_document()
    mismatches = {}
    for field, expected in (("run", run.name), ("app", run.app), ("operation", run.operation)):
        if document.get(field) != expected:
            mismatches[field] = {"expected": expected, "actual": document.get(field)}
    if expected_identity:
        actual_identity = document.get("identity", {})
        for field in (
            "name", "version", "workload_digest", "manifest_digest", "source", "source_revision"
        ):
            expected = expected_identity.get(field)
            if expected is not None and actual_identity.get(field) != expected:
                mismatches[f"identity.{field}"] = {
                    "expected": expected,
                    "actual": actual_identity.get(field),
                }
    if document.get("state") not in _TERMINAL_RESULT_STATES:
        mismatches["state"] = {
            "expected": sorted(_TERMINAL_RESULT_STATES),
            "actual": document.get("state"),
        }
    if mismatches:
        raise errors.ResultIntegrityError(
            "collected result does not identify this terminal App Run",
            code="app_run.result_identity_mismatch",
            details={"mismatches": mismatches},
        )


def _atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
