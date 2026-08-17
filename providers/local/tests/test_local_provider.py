"""Tests for the local Host API provider.

The headline test runs the real apps-001 §2 conformance suite against the
provider over real HTTP with Barista Cloud blocked — proving the first genuine
provider is conformant. Others cover restart recovery, single-user auth, and
honest capability translation. All offline; the fake node backend needs no
hypervisor.
"""

from __future__ import annotations

import socket
import sys
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "conformance"))

from barista_conformance.config import ProviderConfig  # noqa: E402
from barista_conformance.report import Status, evaluate_conformance  # noqa: E402
from barista_conformance.runner import run_conformance  # noqa: E402
from barista_conformance.standalone import install_guard  # noqa: E402

from barista_local_provider import create_local_app  # noqa: E402


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _ServerThread:
    def __init__(self, app, port: int):
        import uvicorn

        self._config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        self._server = uvicorn.Server(self._config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def __enter__(self):
        self._thread.start()
        deadline = time.time() + 10
        while not self._server.started and time.time() < deadline:
            time.sleep(0.02)
        assert self._server.started, "uvicorn did not start"
        return self

    def __exit__(self, *exc):
        self._server.should_exit = True
        self._thread.join(timeout=10)


def test_local_provider_passes_core_conformance_offline(tmp_path):
    # Enforce the Cloud-absent contract in-process for this test.
    install_guard(cloud_hosts=("barista.sh",), proprietary_modules=("barista_cloud",))

    app, store, node = create_local_app(tmp_path / "data")
    port = _free_port()
    try:
        with _ServerThread(app, port):
            config = ProviderConfig(
                endpoint=f"http://127.0.0.1:{port}",
                provider_name="barista-local",
                standalone=False,  # guard already installed above
            )
            report = run_conformance(config)
    finally:
        store.close()
        node.close()

    conformant, violations = evaluate_conformance(report)
    assert conformant, violations
    assert set(report.advertised_profiles) == {"core", "session.pause_resume"}
    summary = report.summary()
    assert summary["failed"] == 0
    # pause_resume is advertised and must be certified (all its cases pass).
    pr = report.cases_for("session.pause_resume")
    assert pr and all(c.status is Status.PASSED for c in pr)


def test_capabilities_are_honest_for_fake_node(tmp_path):
    app, store, node = create_local_app(tmp_path / "data")
    try:
        caps = app.state.provider._caps
        assert "session.pause_resume" in caps
        # The fake node has no memory snapshot / fork, so the provider must not
        # advertise exact snapshot or fork.
        assert "session.snapshot.exact" not in caps
        assert "session.fork" not in caps
    finally:
        store.close()
        node.close()


def test_restart_preserves_app_session_artifact_and_events(tmp_path):
    data = tmp_path / "data"
    manifest = _minimal_manifest()

    # First run: install app, create a session, register an artifact, emit an event.
    app1, store1, node1 = create_local_app(data)
    store1.install_app(manifest, granted=[])
    inst = "inst-restart-1"
    node1.create_and_start(_instance_request(inst, manifest))
    session = store1.create_session(node_instance_id=inst, app=manifest["name"], name="s1", metadata={})
    sid = session["id"]
    store1.append_event(sid, "session.state_changed", {"state": "running"})
    art = store1.register_artifact(
        sid, {"name": "r.txt", "digest": "sha256:" + "cd" * 32, "size_bytes": 3, "media_type": "text/plain"}
    )
    store1.close()
    node1.close()

    # Second run: same data dir, fresh objects. Everything remains addressable
    # with the same logical identifiers.
    app2, store2, node2 = create_local_app(data)
    try:
        assert store2.get_app(manifest["name"]) is not None
        recovered = store2.get_session(sid)
        assert recovered is not None and recovered["name"] == "s1"
        arts = store2.list_artifacts(sid)
        assert any(a["id"] == art["id"] for a in arts)
        events = store2.read_events(sid)
        assert any(e["type"] == "session.state_changed" for e in events)
        # The node backend also recovered the instance from disk.
        assert node2.get(inst) is not None
    finally:
        store2.close()
        node2.close()


def test_token_is_required_when_configured(tmp_path):
    import httpx

    app, store, node = create_local_app(tmp_path / "data", token="s3cret")
    port = _free_port()
    try:
        with _ServerThread(app, port):
            base = f"http://127.0.0.1:{port}/v1alpha1"
            # Discovery is open (negotiation happens before auth)...
            assert httpx.get(f"{base}/discovery").status_code == 200
            # ...but a mutation without the token is rejected.
            unauth = httpx.post(f"{base}/sessions", json={"app": "pi"})
            assert unauth.status_code == 401
            good = httpx.post(
                f"{base}/sessions",
                json={"app": "pi"},
                headers={"authorization": "Bearer s3cret"},
            )
            # 422 app.not_installed (authorized, but no such app) — not 401.
            assert good.status_code == 422
    finally:
        store.close()
        node.close()


def test_manifest_without_digest_is_rejected(tmp_path):
    import httpx

    app, store, node = create_local_app(tmp_path / "data")
    port = _free_port()
    try:
        with _ServerThread(app, port):
            base = f"http://127.0.0.1:{port}/v1alpha1"
            bad = _minimal_manifest()
            del bad["workload"]["digest"]
            resp = httpx.post(
                f"{base}/apps",
                content=__import__("json").dumps(bad),
                headers={"content-type": "application/vnd.barista.app-manifest.v1alpha1+json"},
            )
            assert resp.status_code == 422
            assert resp.json()["class"] == "invalid_request"
    finally:
        store.close()
        node.close()


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _minimal_manifest() -> dict:
    import json

    path = REPO / "contracts" / "app-manifest" / "v1alpha1" / "examples" / "minimal.json"
    return json.loads(path.read_text())


def _instance_request(instance_id: str, manifest: dict):
    from barista_local_provider.node import InstanceRequest

    w = manifest["workload"]
    return InstanceRequest(
        instance_id=instance_id,
        image=w["image"],
        digest=w["digest"],
        arch=w["architectures"][0],
        start_cmd=list(w["entrypoint"]),
    )
