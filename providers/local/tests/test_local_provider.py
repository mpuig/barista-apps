"""Tests for the local Host API provider.

The headline test runs the real apps-001 §2 conformance suite against the
provider over real HTTP with Barista Cloud blocked — proving the first genuine
provider is conformant. Others cover restart recovery, single-user auth, and
honest capability translation. All offline; the fake node backend needs no
hypervisor.
"""

from __future__ import annotations

import json
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


def test_session_handle_is_provider_injected_and_cannot_be_overridden(tmp_path):
    import httpx

    app, store, node = create_local_app(tmp_path / "data")
    port = _free_port()
    try:
        with _ServerThread(app, port):
            base = f"http://127.0.0.1:{port}/v1alpha1"
            installed = httpx.post(
                f"{base}/apps",
                content=json.dumps(_minimal_manifest()),
                headers={"content-type": "application/vnd.barista.app-manifest.v1alpha1+json"},
            )
            assert installed.status_code == 201

            forged = httpx.post(
                f"{base}/sessions",
                json={
                    "app": "pi",
                    "env": {"BARISTA_APP_SESSION_ID": "caller-chosen"},
                },
            )
            assert forged.status_code == 422
            assert store.list_sessions() == []

            created = httpx.post(
                f"{base}/sessions",
                json={"app": "pi", "env": {"SAFE_INPUT": "present"}},
            )
            assert created.status_code == 201
            sid = created.json()["id"]
            instance_id = store.node_instance_id(sid)
            instance_env = node._instances[instance_id]["env"]
            assert instance_env["BARISTA_APP_SESSION_ID"] == sid
            assert instance_env["SAFE_INPUT"] == "present"
    finally:
        store.close()
        node.close()


def test_installed_app_manifest_can_be_read_without_resolved_secrets(tmp_path):
    import httpx

    app, store, node = create_local_app(tmp_path / "data")
    port = _free_port()
    manifest = _minimal_manifest()
    manifest["permissions"] = {
        "secrets": [{"name": "MODEL_API_KEY", "ref": "secret://model/api-key"}]
    }
    try:
        with _ServerThread(app, port):
            base = f"http://127.0.0.1:{port}/v1alpha1"
            installed = httpx.post(
                f"{base}/apps",
                content=json.dumps(manifest),
                headers={"content-type": "application/vnd.barista.app-manifest.v1alpha1+json"},
            )
            assert installed.status_code == 201

            fetched = httpx.get(f"{base}/apps/{manifest['name']}")
            assert fetched.status_code == 200
            body = fetched.json()
            assert body["digest"] == manifest["workload"]["digest"]
            assert body["manifest"] == manifest
            assert body["manifest"]["permissions"]["secrets"] == [
                {"name": "MODEL_API_KEY", "ref": "secret://model/api-key"}
            ]
            assert httpx.get(f"{base}/apps/not-installed").status_code == 404
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


# --------------------------------------------------------------------------- #
# Review-finding regressions
# --------------------------------------------------------------------------- #
def _install_and_ensure(client_base, headers=None):
    import httpx

    headers = headers or {}
    httpx.post(f"{client_base}/apps", content=json.dumps(_minimal_manifest()),
               headers={"content-type": "application/vnd.barista.app-manifest.v1alpha1+json", **headers})
    r = httpx.post(f"{client_base}/sessions", json={"app": "pi"}, headers=headers)
    return r.json()["id"]


def test_auth_is_enforced_on_every_mutation_when_token_set(tmp_path):
    import httpx

    app, store, node = create_local_app(tmp_path / "data", token="s3cret")
    port = _free_port()
    auth = {"authorization": "Bearer s3cret"}
    try:
        with _ServerThread(app, port):
            base = f"http://127.0.0.1:{port}/v1alpha1"
            sid = _install_and_ensure(base, auth)
            # Every mutating/reading session route rejects a missing token — not
            # just install/ensure (finding 1).
            assert httpx.post(f"{base}/sessions/{sid}/exec", json={"command": ["echo", "x"]}).status_code == 401
            assert httpx.get(f"{base}/sessions/{sid}").status_code == 401
            assert httpx.get(f"{base}/sessions/{sid}/events").status_code == 401
            assert httpx.get(f"{base}/sessions/{sid}/artifacts").status_code == 401
            assert httpx.delete(f"{base}/sessions/{sid}").status_code == 401
            # Discovery stays open (pre-auth negotiation).
            assert httpx.get(f"{base}/discovery").status_code == 200
    finally:
        store.close(); node.close()


def test_idempotent_exec_and_artifact_do_not_duplicate(tmp_path):
    import httpx

    app, store, node = create_local_app(tmp_path / "data")
    port = _free_port()
    try:
        with _ServerThread(app, port):
            base = f"http://127.0.0.1:{port}/v1alpha1"
            sid = _install_and_ensure(base)
            k = {"Idempotency-Key": "exec-1"}
            r1 = httpx.post(f"{base}/sessions/{sid}/exec", json={"command": ["echo", "hi"]}, headers=k)
            r2 = httpx.post(f"{base}/sessions/{sid}/exec", json={"command": ["echo", "hi"]}, headers=k)
            assert r1.json()["operation_id"] == r2.json()["operation_id"]  # finding 2

            ak = {"Idempotency-Key": "art-1"}
            body = {"name": "o", "digest": "sha256:" + "ab" * 32, "size_bytes": 1, "media_type": "text/plain"}
            a1 = httpx.post(f"{base}/sessions/{sid}/artifacts", json=body, headers=ak)
            a2 = httpx.post(f"{base}/sessions/{sid}/artifacts", json=body, headers=ak)
            assert a1.json()["id"] == a2.json()["id"]
            assert len(httpx.get(f"{base}/sessions/{sid}/artifacts").json()["items"]) == 1
    finally:
        store.close(); node.close()


def test_exec_cursor_surfaces_stdout(tmp_path):
    import base64
    import httpx

    app, store, node = create_local_app(tmp_path / "data")
    port = _free_port()
    try:
        with _ServerThread(app, port):
            base = f"http://127.0.0.1:{port}/v1alpha1"
            sid = _install_and_ensure(base)
            h = httpx.post(f"{base}/sessions/{sid}/exec", json={"command": ["echo", "hi"]}).json()
            # Reading events from the handle cursor must include exec.stdout (finding 3).
            with httpx.stream("GET", f"{base}/sessions/{sid}/events",
                              params={"cursor": h["event_cursor"]}) as resp:
                body = "".join(resp.iter_text())
            assert "exec.stdout" in body
    finally:
        store.close(); node.close()


def test_install_rejects_unmet_required_capability(tmp_path):
    import httpx

    app, store, node = create_local_app(tmp_path / "data")
    port = _free_port()
    try:
        with _ServerThread(app, port):
            base = f"http://127.0.0.1:{port}/v1alpha1"
            m = _minimal_manifest()
            m["capabilities"] = {"required": [{"capability": "capsule.export"}]}
            r = httpx.post(f"{base}/apps", content=json.dumps(m),
                           headers={"content-type": "application/vnd.barista.app-manifest.v1alpha1+json"})
            assert r.status_code == 501 and r.json()["class"] == "capability"  # finding 4
    finally:
        store.close(); node.close()


def test_unnamed_session_validates_against_schema(tmp_path):
    import httpx
    from jsonschema import Draft202012Validator
    import yaml

    app, store, node = create_local_app(tmp_path / "data")
    port = _free_port()
    try:
        with _ServerThread(app, port):
            base = f"http://127.0.0.1:{port}/v1alpha1"
            httpx.post(f"{base}/apps", content=json.dumps(_minimal_manifest()),
                       headers={"content-type": "application/vnd.barista.app-manifest.v1alpha1+json"})
            s = httpx.post(f"{base}/sessions", json={"app": "pi"}).json()  # no name
            assert "name" not in s  # finding 11: omitted, not null
            spec = yaml.safe_load((REPO / "contracts" / "host-api" / "v1alpha1" / "openapi.yaml").read_text())
            comps = spec["components"]["schemas"]
            root = {"$defs": comps, "$ref": "#/$defs/Session"}
            # rewrite refs
            import json as _j
            root = _j.loads(_j.dumps(root).replace("#/components/schemas/", "#/$defs/"))
            Draft202012Validator(root).validate(s)
    finally:
        store.close(); node.close()


def test_attach_returns_structured_426_not_plain_404(tmp_path):
    import httpx

    app, store, node = create_local_app(tmp_path / "data")
    port = _free_port()
    try:
        with _ServerThread(app, port):
            base = f"http://127.0.0.1:{port}/v1alpha1"
            sid = _install_and_ensure(base)
            r = httpx.get(f"{base}/sessions/{sid}/attach", params={"mode": "raw"})
            assert r.status_code == 426 and r.json()["class"] == "capability"  # finding 12
    finally:
        store.close(); node.close()


def test_non_numeric_cursor_is_a_422_not_a_500(tmp_path):
    import httpx

    app, store, node = create_local_app(tmp_path / "data")
    port = _free_port()
    try:
        with _ServerThread(app, port):
            base = f"http://127.0.0.1:{port}/v1alpha1"
            sid = _install_and_ensure(base)
            r = httpx.get(f"{base}/sessions/{sid}/events", params={"cursor": "abc"})
            assert r.status_code == 422 and r.json()["class"] == "invalid_request"  # finding 13
    finally:
        store.close(); node.close()
