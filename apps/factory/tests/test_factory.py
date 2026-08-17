"""Factory tests: end-to-end mission on the local provider with Cloud blocked,
the same mission on a cloud-shaped provider, harvest-before-reap receipts,
idempotent restart, and mission budget/grant bounds. All offline.
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
sys.path.insert(0, str(REPO / "conformance" / "tests"))
sys.path.insert(0, str(REPO / "providers" / "local"))

from mock_provider import MockProvider  # noqa: E402

from barista_app_sdk import BaristaClient, Config  # noqa: E402
from barista_app_sdk.client import MANIFEST_MEDIA_TYPE  # noqa: E402
from barista_app_factory import Coordinator, Mission, MissionError  # noqa: E402
from barista_app_factory.grants import WORKER_ACTIONS, derive_worker_grant  # noqa: E402

WORKER_MANIFEST = json.loads(
    (REPO / "contracts" / "app-manifest" / "v1alpha1" / "examples" / "minimal.json").read_text()
)


def _mission(tmp_path: Path, n=3, **overrides) -> Mission:
    data = {
        "name": "sweep",
        "app": WORKER_MANIFEST["name"],
        "concurrency": 2,
        "tasks": [
            {"id": f"t{i}", "command": ["sh", "-c", f"echo task-{i}"], "check": ["true"]}
            for i in range(1, n + 1)
        ],
    }
    data.update(overrides)
    return Mission.load(data)


# -- local provider server -------------------------------------------------- #
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


def _install_worker_app(client: BaristaClient) -> None:
    client.install_app(WORKER_MANIFEST)


def test_multi_worker_mission_locally_with_cloud_blocked(tmp_path):
    from barista_conformance.standalone import install_guard

    install_guard(cloud_hosts=("barista.sh",), proprietary_modules=("barista_cloud",))

    from barista_local_provider import create_local_app

    app, store, node = create_local_app(tmp_path / "data")
    port = _free_port()
    try:
        with _Server(app, port):
            with BaristaClient(Config(endpoint=f"http://127.0.0.1:{port}")) as client:
                _install_worker_app(client)
                mission = _mission(tmp_path)
                coord = Coordinator(client, mission, tmp_path / "state.json")
                state = coord.run()

                assert state.state == "done"
                assert state.summary() == {"total": 3, "ok": 3, "failed": 0, "pending": 0}

                # Harvest-before-reap: receipts are retrievable AFTER workers are
                # gone. If the reap had run first, no receipt would exist.
                coord_id = state.coordinator_session_id
                receipts = client.list_artifacts(coord_id)
                names = {a.name for a in receipts}
                assert names == {"receipt-t1.json", "receipt-t2.json", "receipt-t3.json"}
                for ts in state.tasks.values():
                    assert ts.state == "ok"
                    assert ts.receipt["harvested"] is True
                    assert ts.receipt_artifact_id is not None
                    # The worker was reaped.
                    from barista_app_sdk.errors import TerminalError

                    with pytest.raises(TerminalError):
                        client.get_session(f"{mission.name}-{ts.id}")
    finally:
        store.close()
        node.close()


def test_same_mission_runs_against_cloud_shaped_provider(tmp_path):
    cloud = MockProvider(name="cloud-shaped", version="9.9.9")
    with BaristaClient(Config(endpoint="http://cloud.invalid"), transport=cloud.transport()) as client:
        client._http.post(  # install the worker app on the mock
            "/v1alpha1/apps", content=json.dumps(WORKER_MANIFEST),
            headers={"content-type": MANIFEST_MEDIA_TYPE},
        )
        mission = _mission(tmp_path)
        state = Coordinator(client, mission, tmp_path / "state.json").run()
    # Same mission schema, same result/receipt structure as local.
    assert state.summary() == {"total": 3, "ok": 3, "failed": 0, "pending": 0}
    assert all(ts.receipt_artifact_id for ts in state.tasks.values())


def test_restart_does_not_duplicate_accepted_workers(tmp_path):
    from barista_local_provider import create_local_app

    app, store, node = create_local_app(tmp_path / "data")
    port = _free_port()
    state_path = tmp_path / "state.json"
    try:
        with _Server(app, port):
            with BaristaClient(Config(endpoint=f"http://127.0.0.1:{port}")) as client:
                _install_worker_app(client)
                mission = _mission(tmp_path, n=2)

                # First coordinator completes the mission.
                Coordinator(client, mission, state_path).run()
                artifacts_after_first = len(client.list_artifacts(
                    json.loads(state_path.read_text())["coordinator_session_id"]
                ))

                # A second coordinator over the SAME state re-runs: every task is
                # already ok, so it creates no new workers and no duplicate
                # receipts.
                sessions_before = len(client.list_sessions())
                state2 = Coordinator(client, mission, state_path).run()
                sessions_after = len(client.list_sessions())

                assert state2.summary()["ok"] == 2
                assert sessions_before == sessions_after  # no new workers
                artifacts_after_second = len(client.list_artifacts(state2.coordinator_session_id))
                assert artifacts_after_second == artifacts_after_first  # no duplicate receipts
    finally:
        store.close()
        node.close()


def test_budget_caps_worker_count(tmp_path):
    with pytest.raises(MissionError) as ei:
        _mission(tmp_path, n=5, budget={"max_workers": 3})
    assert "max_workers" in str(ei.value)


def test_worker_grant_is_narrower_and_reference_only():
    grant = derive_worker_grant({"secrets": [{"name": "MODEL_API_KEY", "ref": "secret://m/k"}]})
    # A worker cannot create children.
    assert "session.create" not in WORKER_ACTIONS
    assert grant.actions == WORKER_ACTIONS
    # The worker sees a reference, never a raw value.
    assert grant.env() == {"MODEL_API_KEY_REF": "secret://m/k"}


def test_worker_grant_rejects_plaintext_secret():
    with pytest.raises(ValueError):
        derive_worker_grant({"secrets": [{"name": "K", "ref": "sk-live-raw-value"}]})


def test_manifest_and_mission_schema_are_valid(tmp_path):
    from jsonschema import Draft202012Validator

    manifest = json.loads((REPO / "apps" / "factory" / "manifest.json").read_text())
    mschema = json.loads((REPO / "contracts" / "app-manifest" / "v1alpha1" / "schema.json").read_text())
    Draft202012Validator(mschema, format_checker=Draft202012Validator.FORMAT_CHECKER).validate(manifest)
    # A worker cannot inherit the coordinator's create authority via the manifest
    # either: child_sessions bounds fan-out.
    assert manifest["permissions"]["child_sessions"]["max_concurrent"] >= 1
