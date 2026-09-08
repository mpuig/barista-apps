"""Failure-path tests across the provider/NodeClient boundary, entirely offline."""

import json
import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from barista_local_provider import FakeNodeClient, Store, create_local_app
from barista_local_provider.node.client import NodeInstance
from barista_local_provider.node.grpc_client import GrpcNodeClient


MANIFEST = json.loads((Path(__file__).resolve().parents[3] /
                      "contracts/app-manifest/v1alpha1/examples/minimal.json").read_text())


class LostStart(FakeNodeClient):
    lose_response = True

    def create_and_start(self, request):
        # Contract A rejects every identifier outside the ULID alphabet/width.
        assert re.fullmatch(r"[0-7][0-9A-HJKMNP-TV-Z]{25}", request.instance_id)
        result = super().create_and_start(request)
        if self.lose_response:
            self.lose_response = False
            raise TimeoutError("node started; response lost")
        return result


def test_create_uses_node_ulid(tmp_path):
    node = LostStart()
    node.lose_response = False
    app, store, _ = create_local_app(tmp_path, node=node)
    try:
        with TestClient(app) as client:
            assert client.post("/v1alpha1/apps", json=MANIFEST).status_code == 201
            response = client.post("/v1alpha1/sessions", json={"app": MANIFEST["name"]})
            assert response.status_code == 201, response.text
    finally:
        store.close()


@pytest.mark.parametrize("node_started", [False, True])
def test_start_retry_after_restart_preserves_node_and_original_request(tmp_path, node_started):
    node = LostStart(state_path=tmp_path / "node.json")
    app, store, _ = create_local_app(tmp_path, node=node)
    if not node_started:
        def unavailable(request):
            raise TimeoutError("request never reached node")
        node.create_and_start = unavailable
    try:
        with TestClient(app) as client:
            client.post("/v1alpha1/apps", json=MANIFEST)
            failed = client.post("/v1alpha1/sessions", json={"app": MANIFEST["name"], "args": ["original"]},
                                 headers={"Idempotency-Key": "create-1"})
            assert failed.status_code == 503
            reserved = store.list_sessions()
            assert len(reserved) == 1
            sid = reserved[0]["id"]
            iid = store.node_instance_id(sid)
    finally:
        store.close()
        node.close()

    node = LostStart(state_path=tmp_path / "node.json")
    node.lose_response = False
    app, store, _ = create_local_app(tmp_path, node=node)
    try:
        with TestClient(app) as client:
            retried = client.post("/v1alpha1/sessions", json={"app": MANIFEST["name"], "args": ["changed"]},
                                  headers={"Idempotency-Key": "create-1"})
            assert retried.status_code == 200, retried.text
            assert retried.json()["id"] == sid
            assert list(node._instances) == [iid]
            assert node._instances[iid]["start_cmd"][-1] == "original"
            assert store.pending_creation(sid) is None
    finally:
        store.close()
        node.close()


class ReplayStub:
    """Contract A journals operations by key even after their state changes."""

    def __init__(self):
        self.state = "running"
        self.operations = {}
        self.keys = []
        self.transitions = []
        self.lose_response = False

    def transition(self, request, state):
        self.keys.append(request.idempotency_key)
        if request.idempotency_key not in self.operations:
            self.state = state
            self.transitions.append(state)
            self.operations[request.idempotency_key] = SimpleNamespace(state=3)
        if self.lose_response:
            self.lose_response = False
            raise TimeoutError("operation committed; response lost")
        return self.operations[request.idempotency_key]

    def PauseInstance(self, request):
        return self.transition(request, "paused")

    def ResumeInstance(self, request):
        return self.transition(request, "running")


def adapter(stub):
    node = GrpcNodeClient.__new__(GrpcNodeClient)
    node._pb = SimpleNamespace(PauseInstanceRequest=SimpleNamespace, ResumeInstanceRequest=SimpleNamespace,
                               OPERATION_STATE_DONE=3, OPERATION_STATE_FAILED=4)
    node._stub = stub
    node._poll_timeout = 1
    node.node_info = FakeNodeClient().node_info
    node.get = lambda iid: NodeInstance(iid, stub.state)
    return node


def test_grpc_adapter_new_cycles_use_new_keys():
    stub = ReplayStub()
    node = adapter(stub)
    node.pause("instance")
    node.resume("instance")
    node.pause("instance")
    assert stub.transitions == ["paused", "running", "paused"]


def test_lifecycle_retry_after_restart_reuses_operation_key(tmp_path):
    stub = ReplayStub()
    app, store, _ = create_local_app(tmp_path, node=adapter(stub))
    sid = store.create_session("instance", "app", None, None)["id"]
    stub.lose_response = True
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            failed = client.post(f"/v1alpha1/sessions/{sid}/pause", headers={"Idempotency-Key": "pause-1"})
            assert failed.status_code >= 500
            opid = store.idempotent_lookup("pause-1", "pause")
            assert opid and not store.get_operation(opid)["done"]
    finally:
        store.close()

    app, store, _ = create_local_app(tmp_path, node=adapter(stub))
    try:
        with TestClient(app) as client:
            retry = client.post(f"/v1alpha1/sessions/{sid}/pause", headers={"Idempotency-Key": "pause-1"})
            assert retry.status_code == 202
            assert retry.json()["id"] == opid
            assert retry.json()["done"] is True
            assert stub.keys[0] == stub.keys[1] == opid
            client.post(f"/v1alpha1/sessions/{sid}/resume", headers={"Idempotency-Key": "resume-1"})
            client.post(f"/v1alpha1/sessions/{sid}/pause", headers={"Idempotency-Key": "pause-2"})
            assert stub.transitions == ["paused", "running", "paused"]
    finally:
        store.close()


def test_late_lifecycle_replay_records_current_state(tmp_path):
    stub = ReplayStub()
    app, store, _ = create_local_app(tmp_path, node=adapter(stub))
    sid = store.create_session("instance", "app", None, None)["id"]
    try:
        with TestClient(app) as client:
            stub.lose_response = True
            assert client.post(f"/v1alpha1/sessions/{sid}/pause",
                               headers={"Idempotency-Key": "old-pause"}).status_code == 503
            assert client.post(f"/v1alpha1/sessions/{sid}/resume",
                               headers={"Idempotency-Key": "new-resume"}).status_code == 202
            assert client.post(f"/v1alpha1/sessions/{sid}/pause",
                               headers={"Idempotency-Key": "old-pause"}).status_code == 202
            assert stub.state == store.get_session(sid)["state"] == "running"
    finally:
        store.close()


def test_creation_reservation_rolls_back_when_key_cannot_be_recorded(tmp_path):
    store = Store(tmp_path)
    try:
        store._db.execute("""CREATE TRIGGER reject_key BEFORE INSERT ON idempotency
            BEGIN SELECT RAISE(ABORT, 'injected storage failure'); END""")
        with pytest.raises(sqlite3.IntegrityError):
            store.reserve_creation("session", "app", None, {}, {"instance_id": "node"}, "key")
        assert store.list_sessions() == []
        assert store.pending_creation("session") is None
    finally:
        store.close()
