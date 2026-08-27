"""Standalone acceptance flow (apps-001 tasks 9.4 / 9.5).

Runs the whole open stack with Barista Cloud unreachable, no Cloud credential,
and no proprietary package available:

  1. install the Cloud-absent guard and prove no proprietary module is present;
  2. start the local provider (offline);
  3. run the Host API conformance core profile against it -> conformant;
  4. run a multi-worker Factory mission -> all ok, receipts harvested;
  5. semantic Lift with the Pi adapter -> completed with a fidelity report;
  6. build a Session Story -> deterministic, non-executable, schema-valid.

Everything below runs under one process-wide standalone guard.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

from barista_conformance.config import ProviderConfig
from barista_conformance.report import evaluate_conformance
from barista_conformance.runner import run_conformance
from barista_conformance.standalone import (
    StandaloneViolation,
    assert_no_proprietary_modules,
    install_guard,
)

from barista_app_sdk import BaristaClient, Config
from barista_local_provider import create_local_app
from barista_app_factory import Coordinator, Mission
from barista_app_lift import Lift, SourceRef
from barista_app_pi import PiAdapter
from barista_app_story import StoryBuilder, Source

WORKER_MANIFEST = json.loads(
    (REPO / "contracts" / "app-manifest" / "v1alpha1" / "examples" / "minimal.json").read_text()
)
CLOUD_HOSTS = ("barista.sh", "beta.barista.sh", "api.barista.sh")
PROPRIETARY = ("barista_cloud",)


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


def _write_pi_fixture(home: Path, workspace: str) -> None:
    enc = "--" + workspace.strip("/").replace("/", "-") + "--"
    d = home / "sessions" / enc
    d.mkdir(parents=True)
    lines = [
        {"type": "session", "version": 3, "id": "acc-0001", "cwd": workspace},
        {"type": "message", "id": "m1", "role": "user", "text": "plan the migration"},
    ]
    (d / "2026-08-17T00-00-00_acc0001.jsonl").write_bytes(
        ("\n".join(json.dumps(x) for x in lines) + "\n").encode()
    )


def test_full_standalone_acceptance_with_cloud_blocked(tmp_path):
    # (1) Cloud-absent guard, enforced for the whole test.
    assert_no_proprietary_modules(PROPRIETARY)
    install_guard(cloud_hosts=CLOUD_HOSTS, proprietary_modules=PROPRIETARY)
    with pytest.raises(StandaloneViolation):
        import socket as _s

        _s.getaddrinfo("api.barista.sh", 443)

    # (2) Local provider, offline.
    app, store, node = create_local_app(tmp_path / "data")
    port = _free_port()
    endpoint = f"http://127.0.0.1:{port}"
    try:
        with _Server(app, port):
            # (3) Conformance core profile.
            report = run_conformance(ProviderConfig(endpoint=endpoint, provider_name="barista-local"))
            conformant, violations = evaluate_conformance(report)
            assert conformant, violations
            assert set(report.advertised_profiles) == {"core", "session.pause_resume"}

            with BaristaClient(Config(endpoint=endpoint)) as client:
                client.install_app(WORKER_MANIFEST)

                # (4) Factory mission.
                mission = Mission.load({
                    "name": "acc",
                    "app": WORKER_MANIFEST["name"],
                    "concurrency": 2,
                    "tasks": [
                        {"id": "t1", "command": ["sh", "-c", "echo one"], "check": ["true"]},
                        {"id": "t2", "command": ["sh", "-c", "echo two"], "check": ["true"]},
                    ],
                })
                fstate = Coordinator(client, mission, tmp_path / "mission.json").run()
                assert fstate.summary() == {"total": 2, "ok": 2, "failed": 0, "pending": 0}
                receipts = client.list_artifacts(fstate.coordinator_session_id)
                assert {a.name for a in receipts} == {
                    "receipt-t1.json", "receipt-t2.json", "mission-result.json"
                }

                # (5) Semantic Lift with the Pi adapter (native -> new session).
                workspace = "/work/acc-project"
                _write_pi_fixture(tmp_path / "pi-home", workspace)
                lift = Lift(client, client, adapter=PiAdapter(home=tmp_path / "pi-home"),
                            target_app=WORKER_MANIFEST["name"])
                receipt = lift.transfer(
                    SourceRef(managed=False, workspace=workspace), mode="semantic"
                )
                assert receipt.mode == "semantic" and receipt.status == "completed"
                assert "transcript" in receipt.transferred
                assert receipt.target_session_id

                # (6) Session Story from the mission's knowledge records.
                records = [
                    {"type": "decision", "time": "2026-08-17T00:00:01Z", "text": "ran factory acc"},
                    {"type": "receipt", "time": "2026-08-17T00:00:02Z",
                     "text": f"t1 ok, t2 ok ({len(receipts)} receipts)"},
                ]
                for a in receipts:
                    records.append({
                        "type": "artifact_ref", "time": "2026-08-17T00:00:03Z",
                        "artifact": {"name": a.name, "digest": a.digest, "media_type": "application/json"},
                    })
                story = StoryBuilder().build(
                    records, created_at="2026-08-17T00:00:00Z", title="acc run",
                    source=Source(app="factory"),
                )
                from jsonschema import Draft202012Validator

                schema = json.loads(
                    (REPO / "contracts" / "session-story" / "v1alpha1" / "schema.json").read_text()
                )
                Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER).validate(story)
                assert story["story_id"].startswith("sha256:")
                assert "capsule_object" not in json.dumps(story)
    finally:
        store.close()
        node.close()
