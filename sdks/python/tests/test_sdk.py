"""SDK tests.

The headline test proves the same app code runs against two different Host API
providers (a real local provider and a 'cloud-shaped' provider) with only the
client's endpoint/transport configuration changing — never a branch on provider
name. Others cover idempotent retry, typed errors, negotiation, waiting,
streams, sensitive-data handling, and opaque adapter round-trips. All offline.
"""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "conformance"))
sys.path.insert(0, str(REPO / "conformance" / "tests"))
sys.path.insert(0, str(REPO / "providers" / "local"))

from mock_provider import MockProvider  # noqa: E402

from barista_app_sdk import BaristaClient, Config, errors  # noqa: E402
from barista_app_sdk.adapters import (  # noqa: E402
    Attachment,
    FidelityReport,
    SemanticBundle,
)
from barista_app_sdk.sensitive import SecretLeak, assert_no_secret_values, redact_text  # noqa: E402


def _minimal_manifest() -> dict:
    return json.loads((REPO / "contracts" / "app-manifest" / "v1alpha1" / "examples" / "minimal.json").read_text())


# --------------------------------------------------------------------------- #
# A single app workflow. It NEVER branches on provider name — only on
# discovered capabilities. This is the portable-app contract in miniature.
# --------------------------------------------------------------------------- #
def run_demo(client: BaristaClient) -> dict:
    disc = client.negotiate(required=[])
    manifest = _minimal_manifest()
    client.install_app(manifest)
    session = client.ensure_session(manifest["name"], name="demo")

    handle = client.exec(session.id, ["echo", "hello"])
    op = client.wait_operation(handle.operation_id, timeout=10)

    events = list(client.events(session.id, max_events=5))

    art = client.register_artifact(
        session.id, name="out.txt", digest="sha256:" + "ab" * 32, size_bytes=6, media_type="text/plain"
    )
    listed = client.list_artifacts(session.id)

    paused = None
    if client.supports("session.pause_resume"):
        paused = client.pause(session.id)
        client.resume(session.id)

    client.delete_session(session.id)
    return {
        "capabilities": disc.capabilities,
        "session_id": session.id,
        "op_done": op.done,
        "event_count": len(events),
        "artifact_id": art.id,
        "artifact_listed": any(a.id == art.id for a in listed),
        "paused": paused is not None,
    }


# -- local provider (real HTTP server) -------------------------------------- #
def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _Server:
    def __init__(self, app, port):
        import uvicorn

        self._server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def __enter__(self):
        self._thread.start()
        deadline = time.time() + 10
        while not self._server.started and time.time() < deadline:
            time.sleep(0.02)
        assert self._server.started
        return self

    def __exit__(self, *exc):
        self._server.should_exit = True
        self._thread.join(timeout=10)


def test_same_app_runs_against_local_and_cloud_shaped_providers(tmp_path):
    from barista_local_provider import create_local_app

    # Provider A: the real local provider over HTTP (endpoint config only).
    app, store, node = create_local_app(tmp_path / "data")
    port = _free_port()
    try:
        with _Server(app, port):
            with BaristaClient(Config(endpoint=f"http://127.0.0.1:{port}")) as client:
                local_result = run_demo(client)
    finally:
        store.close()
        node.close()

    # Provider B: a 'cloud-shaped' provider (different identity + capabilities),
    # reached by injecting a transport — still just configuration to the app.
    cloud = MockProvider(name="cloud-shaped", version="9.9.9", capabilities=["session.pause_resume"])
    with BaristaClient(Config(endpoint="http://cloud.invalid"), transport=cloud.transport()) as client:
        cloud_result = run_demo(client)

    # The same workflow succeeded on both; only capabilities differ.
    for result in (local_result, cloud_result):
        assert result["op_done"] is True
        assert result["event_count"] >= 1
        assert result["artifact_listed"] is True
    assert local_result["paused"] is True  # fake node supports pause/resume
    assert cloud_result["paused"] is True   # cloud-shaped advertises it too


def test_idempotent_ensure_survives_lost_response():
    """A lost response must not create a duplicate: the SDK retries with the same
    idempotency key and the provider returns the original session."""
    mock = MockProvider(name="flaky")

    class FlakyTransport(httpx.BaseTransport):
        def __init__(self, inner):
            self._inner = inner
            self._failed = False

        def handle_request(self, request):
            resp = self._inner.handle_request(request)
            # Simulate the FIRST ensure response being lost after the server
            # already created the session.
            if request.url.path.endswith("/sessions") and request.method == "POST" and not self._failed:
                self._failed = True
                raise httpx.ConnectError("connection reset", request=request)
            return resp

    transport = FlakyTransport(mock.transport())
    with BaristaClient(Config(endpoint="http://flaky.invalid"), transport=transport) as client:
        session = client.ensure_session("pi", name="once")
    assert session.id
    assert len(mock.sessions) == 1, "a lost response must not duplicate the session"


def test_typed_errors_and_no_retry_on_terminal():
    mock = MockProvider(name="err")
    with BaristaClient(Config(endpoint="http://err.invalid"), transport=mock.transport()) as client:
        with pytest.raises(errors.TerminalError):
            client.get_session("nope")


def test_negotiate_missing_capability_raises():
    mock = MockProvider(name="core-only")  # advertises no optional profiles
    with BaristaClient(Config(endpoint="http://x.invalid"), transport=mock.transport()) as client:
        with pytest.raises(errors.CapabilityError) as ei:
            client.negotiate(required=["session.fork"])
        assert "session.fork" in ei.value.details.get("missing", [])


def test_capability_error_on_unsupported_pause():
    mock = MockProvider(name="core-only")  # no pause_resume
    with BaristaClient(Config(endpoint="http://x.invalid"), transport=mock.transport()) as client:
        s = client.ensure_session("pi")
        with pytest.raises(errors.CapabilityError):
            client.pause(s.id)


# -- sensitive-data handling ------------------------------------------------ #
def test_secret_leak_is_rejected_but_reference_is_allowed():
    payload = {"env": {"MODEL_KEY": "secret://ref/key"}, "log": "started with reference"}
    assert_no_secret_values(payload, ["sk-live-abcdef123456"])  # not present -> ok

    leaky = {"log": "used key sk-live-abcdef123456 to auth"}
    with pytest.raises(SecretLeak):
        assert_no_secret_values(leaky, ["sk-live-abcdef123456"])


def test_redaction_is_deterministic():
    text = "token=sk-live-abcdef123456 done"
    assert redact_text(text, ["sk-live-abcdef123456"]) == "token=«redacted» done"


# -- adapters: opaque native state round-trip ------------------------------- #
def test_semantic_bundle_preserves_opaque_native_attachment():
    native_bytes = b"\x00\x01pi-native-session\xff"
    bundle = SemanticBundle(
        adapter="sh.barista.adapter.pi",
        created_at="2026-08-17T00:00:00Z",
        fidelity=FidelityReport(level="high", missing=["environment"]),
        inventory={"continuation_prompt": "resume the migration"},
        native=[Attachment(name="session", media_type="application/x-pi-session", data=native_bytes)],
    )
    doc = bundle.to_document()
    # The wire document carries digest + media type; bytes are preserved verbatim
    # in the attachment object the provider stores out of band.
    assert doc["native"][0]["media_type"] == "application/x-pi-session"
    assert doc["native"][0]["size_bytes"] == len(native_bytes)
    assert bundle.native[0].data == native_bytes
    assert doc["fidelity"]["level"] == "high"
    assert doc["fidelity"]["missing"] == ["environment"]


def test_semantic_bundle_validates_against_contract_schema():
    from jsonschema import Draft202012Validator

    schema = json.loads(
        (REPO / "contracts" / "session-story" / "v1alpha1" / "semantic-state.schema.json").read_text()
    )
    bundle = SemanticBundle(
        adapter="sh.barista.adapter.pi",
        created_at="2026-08-17T00:00:00Z",
        fidelity=FidelityReport(level="partial", missing=["transcript"]),
        inventory={"continuation_prompt": "carry on"},
        native=[Attachment(name="s", media_type="application/x-pi", data=b"abcd")],
    )
    Draft202012Validator(schema).validate(bundle.to_document())


def test_redact_payload_matches_secrets_with_quotes_and_non_ascii():
    from barista_app_sdk.sensitive import redact_payload

    secret = 'pa"ss\\wörd-1234'
    out = redact_payload({"log": f"used {secret} to auth", "nested": [secret]}, [secret])
    assert secret not in json.dumps(out, ensure_ascii=False)
    assert "«redacted»" in out["log"]
    assert out["nested"][0] == "«redacted»"


def test_to_document_rejects_unknown_inventory_component():
    from barista_app_sdk.adapters import FidelityReport, SemanticBundle

    bundle = SemanticBundle(
        adapter="x", created_at="2026-08-17T00:00:00Z",
        fidelity=FidelityReport(level="high"),
        inventory={"not_a_real_component": {"x": 1}},
    )
    with pytest.raises(ValueError):
        bundle.to_document()


def test_wait_operation_backs_off(monkeypatch):
    # The poll interval grows (no fixed 50ms hammering) — assert the sleep values
    # increase across polls until the op completes.
    from barista_app_sdk import BaristaClient, Config
    from barista_app_sdk.models import Operation

    sleeps = []
    client = BaristaClient(Config(endpoint="http://x.invalid"), transport=MockProvider().transport())
    calls = {"n": 0}

    def fake_get_operation(op_id):
        calls["n"] += 1
        return Operation(id=op_id, kind="exec", done=calls["n"] >= 4)

    monkeypatch.setattr(client, "get_operation", fake_get_operation)
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))
    client.wait_operation("op-x", timeout=100)
    assert sleeps == sorted(sleeps) and sleeps[0] < sleeps[-1]  # backoff, not flat
    client.close()
