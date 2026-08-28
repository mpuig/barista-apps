"""Factory tests: end-to-end mission on the local provider with Cloud blocked,
the same mission on a cloud-shaped provider, harvest-before-reap receipts,
idempotent restart, and mission budget/grant bounds. All offline.
"""

from __future__ import annotations

import base64
import json
import os
import re
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
from barista_app_factory import (  # noqa: E402
    Coordinator,
    CredentialKeeper,
    Mission,
    MissionError,
)
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
                assert names == {
                    "receipt-t1.json", "receipt-t2.json", "receipt-t3.json", "mission-result.json"
                }
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


# -- apps-004: a provider double with a filesystem -------------------------- #
class _FsProvider(MockProvider):
    """MockProvider that remembers what was written to it.

    `transfer.py` moves bytes with two command shapes and nothing else, so a
    double only has to understand those two. The stock double echoes commands
    (its node is a deterministic echo), which is fine for exercising exec but
    cannot round-trip a file — and a staged-mission test that cannot round-trip a
    file is testing the scheduler while pretending to test the transfer.

    Subclassed here rather than changed in `conformance/`: that double is shared
    with the conformance suite, where every behaviour is a claim about what a
    provider must do. This is a test fixture, not a claim.
    """

    _WRITE = re.compile(r"printf %s ([A-Za-z0-9+/=]*) \| base64 -d > '([^']+)'")

    def __init__(self, **kw):
        super().__init__(**kw)
        self.files: dict[tuple[str, str], bytes] = {}

    def _exec_stdout(self, session_id: str, command: list) -> bytes:
        if len(command) == 3 and command[0] == "sh" and command[1] == "-c":
            m = self._WRITE.search(command[2])
            if m:
                self.files[(session_id, m.group(2))] = base64.b64decode(m.group(1))
                return b""
        if len(command) == 2 and command[0] == "cat":
            return self.files.get((session_id, command[1]), b"")
        return super()._exec_stdout(session_id, command)


def _fs_client(provider: _FsProvider) -> BaristaClient:
    client = BaristaClient(Config(endpoint="http://cloud.invalid"), transport=provider.transport())
    client._http.post(
        "/v1alpha1/apps", content=json.dumps(WORKER_MANIFEST),
        headers={"content-type": MANIFEST_MEDIA_TYPE},
    )
    return client


class _ExitProvider(_FsProvider):
    """The filesystem double, with a real non-zero command for failure paths."""

    def _handle(self, request):
        if request.method == "POST" and request.url.path == "/v1alpha1/sessions":
            name = json.loads(request.content or b"{}").get("name")
            existing = next((s for s in self.sessions.values() if s.get("name") == name), None)
            if existing is not None:
                return self._json(200, existing)
        response = super()._handle(request)
        if request.method == "POST" and request.url.path.endswith("/exec"):
            command = json.loads(request.content or b"{}").get("command") or []
            if command == ["false"] and response.status_code == 200:
                operation_id = response.json()["operation_id"]
                self.operations[operation_id]["result"]["exit_code"] = 1
        return response


def test_failure_retries_blocks_dependents_and_preserves_forensics(tmp_path):
    """The final failed attempt gets a receipt but not a reap; work downstream
    is blocked without an attempt, while an independent success remains durable
    after its worker disappears."""
    provider = _ExitProvider()
    with _fs_client(provider) as client:
        mission = Mission.load({
            "name": "failure-paths", "app": WORKER_MANIFEST["name"],
            "max_attempts": 2, "concurrency": 2,
            "tasks": [
                {"id": "bad-check", "command": ["true"], "check": ["false"]},
                {"id": "blocked", "command": ["true"], "depends_on": ["bad-check"]},
                {"id": "success", "command": ["true"],
                 "files": {"/work/out": "kept"}, "produces": {"output": "/work/out"}},
            ],
        })
        state = Coordinator(client, mission, tmp_path / "state.json").run()
        artifact_names = {a.name for a in client.list_artifacts(state.coordinator_session_id)}

    assert state.state == "done"
    assert state.summary() == {"total": 3, "ok": 1, "failed": 1, "pending": 0, "blocked": 1}

    failed = state.tasks["bad-check"]
    assert failed.attempts == 2
    assert failed.receipt["outcome"] == "failed"
    assert failed.receipt["attempts"] == 2
    assert failed.receipt["checks"][-1]["exit_code"] == 1
    assert failed.receipt_artifact_id
    assert any(s.get("name") == failed.worker for s in provider.sessions.values()), (
        "the failed worker was reaped instead of left for forensics"
    )

    blocked = state.tasks["blocked"]
    assert blocked.state == "blocked" and blocked.blocked_by == "bad-check"
    assert blocked.attempts == 0 and blocked.receipt is None

    success = state.tasks["success"]
    assert success.receipt["outcome"] == "ok"
    assert not any(s.get("name") == success.worker for s in provider.sessions.values())
    assert {"success-output", "receipt-success.json", "receipt-bad-check.json"} <= artifact_names


def test_a_dependent_runs_after_its_dependency_and_receives_what_it_produced(tmp_path):
    provider = _FsProvider()
    with _fs_client(provider) as client:
        mission = Mission.load({
            "name": "chain", "app": WORKER_MANIFEST["name"], "concurrency": 3,
            "tasks": [
                {"id": "spec", "command": ["true"], "files": {"/work/seed": "the spec"},
                 "produces": {"spec": "/work/seed"}},
                {"id": "impl", "command": ["true"], "depends_on": ["spec"],
                 "consumes": {"spec": "/work/spec.md"}},
            ],
        })
        state = Coordinator(client, mission, tmp_path / "state.json").run()

    assert state.summary()["ok"] == 2
    # The consumer's session holds the producer's bytes. Keyed by path: session
    # ids are opaque (`sess-<hex>`), and a test that assumes they encode the task
    # name is asserting on the provider's id format rather than on the transfer.
    landed = [c for (_sid, path), c in provider.files.items() if path == "/work/spec.md"]
    assert landed == [b"the spec"], provider.files
    # And the digest was recorded on the producer, not a copy of the content.
    assert state.tasks["spec"].outputs["spec"].startswith("sha256:")


def test_a_produced_output_survives_its_workers_reap(tmp_path):
    """The producer is reaped on success before the consumer starts, so the
    transfer cannot depend on both workers being alive."""
    provider = _FsProvider()
    with _fs_client(provider) as client:
        mission = Mission.load({
            "name": "chain", "app": WORKER_MANIFEST["name"], "concurrency": 1,
            "tasks": [
                {"id": "a", "command": ["true"], "files": {"/w/x": "carried"},
                 "produces": {"out": "/w/x"}},
                {"id": "b", "command": ["true"], "depends_on": ["a"],
                 "consumes": {"out": "/w/in"}},
            ],
        })
        state = Coordinator(client, mission, tmp_path / "state.json").run()
        # The producer really is gone.
        from barista_app_sdk.errors import TerminalError

        with pytest.raises(TerminalError):
            client.get_session("chain-a")
    assert state.summary()["ok"] == 2
    got = [c for (sid, p), c in provider.files.items() if p == "/w/in"]
    assert got == [b"carried"]


def test_a_failed_dependency_blocks_its_dependents_transitively(tmp_path):
    """Blocking is transitive. Marking only direct dependents would leave a task
    three hops downstream pending forever, and a mission that never finishes is a
    worse report than one that says why it stopped."""
    provider = _FsProvider()
    with _fs_client(provider) as client:
        mission = Mission.load({
            "name": "chain", "app": WORKER_MANIFEST["name"],
            "tasks": [
                {"id": "a", "command": ["true"]},
                {"id": "b", "command": ["true"], "depends_on": ["a"]},
                {"id": "c", "command": ["true"], "depends_on": ["b"]},
                {"id": "d", "command": ["true"]},
            ],
        })
        coord = Coordinator(client, mission, tmp_path / "state.json")
        # The double exits 0 for everything, so failure is injected on the state
        # rather than faked in the provider — this exercises the scheduler's
        # reachability rule, which is what the test is about.
        coord.state.tasks["a"].state = "failed"
        coord._mark_unreachable()

    assert coord.state.tasks["b"].state == "blocked"
    assert coord.state.tasks["b"].blocked_by == "a"
    # Transitive: c depends on b, which is blocked rather than failed.
    assert coord.state.tasks["c"].state == "blocked"
    assert coord.state.tasks["c"].blocked_by == "b"
    # And an independent task is untouched.
    assert coord.state.tasks["d"].state == "pending"


def test_an_independent_task_still_runs_when_another_branch_fails(tmp_path):
    provider = _FsProvider()
    with _fs_client(provider) as client:
        mission = Mission.load({
            "name": "chain", "app": WORKER_MANIFEST["name"],
            "tasks": [
                {"id": "a", "command": ["true"]},
                {"id": "b", "command": ["true"], "depends_on": ["a"]},
                {"id": "d", "command": ["true"]},
            ],
        })
        state = Coordinator(client, mission, tmp_path / "state.json").run()
    assert state.tasks["d"].state == "ok"
    assert state.state == "done"


def test_a_dependent_never_starts_before_its_dependency_has_finished(tmp_path, monkeypatch):
    """The change's central property, asserted on order rather than on a result.

    Written after mutation testing found that making every task ready regardless
    of its dependencies broke no test: the outcome assertions elsewhere could
    still pass by luck, because a dependency that happens to finish first leaves
    the same result behind. The slow dependency is what removes the luck — with
    ordering unenforced, the dependent starts while `slow` is still sleeping and
    the interleaving is recorded.
    """
    order: list[tuple[str, str]] = []
    real_run_task = Coordinator.run_task

    def recording(self, task):
        order.append((task.id, "start"))
        try:
            return real_run_task(self, task)
        finally:
            order.append((task.id, "end"))

    monkeypatch.setattr(Coordinator, "run_task", recording)

    provider = _FsProvider()
    real_exec = _FsProvider._exec_stdout

    def slow(self, session_id, command):
        if command == ["slow"]:
            time.sleep(0.15)
        return real_exec(self, session_id, command)

    monkeypatch.setattr(_FsProvider, "_exec_stdout", slow)

    with _fs_client(provider) as client:
        mission = Mission.load({
            "name": "order", "app": WORKER_MANIFEST["name"], "concurrency": 4,
            "tasks": [
                {"id": "slow-dep", "command": ["slow"]},
                {"id": "dependent", "command": ["true"], "depends_on": ["slow-dep"]},
                {"id": "other", "command": ["true"]},
            ],
        })
        state = Coordinator(client, mission, tmp_path / "state.json").run()

    assert state.summary()["ok"] == 3
    assert order.index(("slow-dep", "end")) < order.index(("dependent", "start")), order
    # `other` depends on nothing, so it must NOT have waited for the slow task —
    # otherwise this test would pass just as well against a serial scheduler.
    assert order.index(("other", "start")) < order.index(("slow-dep", "end")), order


def test_a_diamond_runs_its_join_once_and_only_after_both_branches(tmp_path):
    provider = _FsProvider()
    with _fs_client(provider) as client:
        mission = Mission.load({
            "name": "diamond", "app": WORKER_MANIFEST["name"], "concurrency": 4,
            "tasks": [
                {"id": "a", "command": ["true"], "files": {"/w/a": "A"}, "produces": {"a": "/w/a"}},
                {"id": "b", "command": ["true"], "depends_on": ["a"], "files": {"/w/b": "B"},
                 "produces": {"b": "/w/b"}},
                {"id": "c", "command": ["true"], "depends_on": ["a"], "files": {"/w/c": "C"},
                 "produces": {"c": "/w/c"}},
                {"id": "d", "command": ["true"], "depends_on": ["b", "c"],
                 "consumes": {"b": "/w/from-b", "c": "/w/from-c"}},
            ],
        })
        state = Coordinator(client, mission, tmp_path / "state.json").run()

    assert state.summary()["ok"] == 4
    # Exactly one worker for the join, not one per incoming edge.
    assert state.tasks["d"].attempts == 1
    # And it received both branches, which it could not have unless both had
    # already succeeded and been captured.
    assert [c for (_s, path), c in provider.files.items() if path == "/w/from-b"] == [b"B"]
    assert [c for (_s, path), c in provider.files.items() if path == "/w/from-c"] == [b"C"]


def test_a_restart_mid_graph_resumes_without_rerunning_what_succeeded(tmp_path):
    """Readiness is recomputed from recovered task states, so a second
    coordinator over the same state finishes the graph rather than restarting it."""
    provider = _FsProvider()
    state_path = tmp_path / "state.json"
    with _fs_client(provider) as client:
        spec = {
            "name": "resume", "app": WORKER_MANIFEST["name"], "concurrency": 2,
            "tasks": [
                {"id": "a", "command": ["true"], "files": {"/w/a": "A"}, "produces": {"a": "/w/a"}},
                {"id": "b", "command": ["true"], "depends_on": ["a"], "consumes": {"a": "/w/in"}},
            ],
        }
        first = Coordinator(client, Mission.load(spec), state_path)
        # Only `a` runs: the graph is stopped before `b` becomes ready.
        first.run_task(first.mission.tasks[0])
        assert first.state.tasks["a"].state == "ok"
        attempts_a = first.state.tasks["a"].attempts

        # A fresh coordinator over the same state file.
        state = Coordinator(client, Mission.load(spec), state_path).run()

    assert state.summary()["ok"] == 2
    assert state.tasks["a"].attempts == attempts_a, "a completed task was run again"
    assert [c for (_s, path), c in provider.files.items() if path == "/w/in"] == [b"A"]


def test_planted_content_is_in_place_before_the_command_runs(tmp_path):
    provider = _FsProvider()
    seen: dict[str, bytes | None] = {}
    real = _FsProvider._exec_stdout

    def observe(self, session_id, command):
        if command == ["true"]:
            # What the task's own command sees when it starts.
            seen["at_command"] = self.files.get((session_id, "/work/given"))
        return real(self, session_id, command)

    with _fs_client(provider) as client:
        mission = Mission.load({
            "name": "plant", "app": WORKER_MANIFEST["name"],
            "tasks": [{"id": "t", "command": ["true"], "files": {"/work/given": "provided"}}],
        })
        _FsProvider._exec_stdout = observe
        try:
            Coordinator(client, mission, tmp_path / "state.json").run()
        finally:
            _FsProvider._exec_stdout = real
    assert seen.get("at_command") == b"provided"


def test_blocked_is_neither_failed_nor_pending(tmp_path):
    """A task that never ran has learned nothing about itself. Reporting it as
    failed sends someone to debug work that never happened; reporting it as
    pending says it is still coming."""
    from barista_app_factory.state import MissionState

    st = MissionState(mission="m", tasks={})
    from barista_app_factory.state import TaskState

    st.tasks = {"a": TaskState(id="a", state="failed"), "b": TaskState(id="b", state="blocked", blocked_by="a")}
    summary = st.summary()
    assert summary["failed"] == 1 and summary["blocked"] == 1
    assert summary["pending"] == 0
    assert st.tasks["b"].blocked_by == "a"


def test_summary_omits_blocked_when_there_are_none():
    """A mission without dependencies reports exactly what it always did."""
    from barista_app_factory.state import MissionState, TaskState

    st = MissionState(mission="m", tasks={"a": TaskState(id="a", state="ok")})
    assert st.summary() == {"total": 1, "ok": 1, "failed": 0, "pending": 0}


def test_independent_tasks_still_run_concurrently(tmp_path, monkeypatch):
    """The ready-set loop must not have serialised a mission that has no edges."""
    provider = _FsProvider()
    seen: list[int] = []
    lock = threading.Lock()
    live = {"n": 0}

    real = MockProvider._exec_stdout

    def counting(self, session_id, command):
        with lock:
            live["n"] += 1
            seen.append(live["n"])
        time.sleep(0.02)
        with lock:
            live["n"] -= 1
        return real(self, session_id, command)

    with _fs_client(provider) as client:
        mission = Mission.load({
            "name": "wide", "app": WORKER_MANIFEST["name"], "concurrency": 3,
            "tasks": [{"id": f"t{i}", "command": ["true"]} for i in range(6)],
        })
        monkeypatch.setattr(_FsProvider, "_exec_stdout", counting)
        state = Coordinator(client, mission, tmp_path / "state.json").run()

    assert state.summary()["ok"] == 6
    assert max(seen) > 1, "no two tasks were ever in flight together"


def test_the_planted_criterion_is_restored_before_the_check_runs(tmp_path, monkeypatch):
    """The half of the gate guarantee that needs no opt-in: a worker that
    overwrites the planted criterion is still judged against the mission's copy."""
    provider = _FsProvider()
    overwritten = {"done": False}
    real = _FsProvider._exec_stdout

    def clobber(self, session_id, command):
        # The task's own command replaces the planted file with its own version.
        if command == ["true"] and not overwritten["done"]:
            self.files[(session_id, "/work/criterion")] = b"the worker's version"
            overwritten["done"] = True
        return real(self, session_id, command)

    monkeypatch.setattr(_FsProvider, "_exec_stdout", clobber)
    with _fs_client(provider) as client:
        mission = Mission.load({
            "name": "gate", "app": WORKER_MANIFEST["name"],
            "tasks": [{
                "id": "t", "command": ["true"],
                "files": {"/work/criterion": "the mission's version"},
                "check": ["node", "/work/criterion"],
            }],
        })
        Coordinator(client, mission, tmp_path / "state.json").run()

    assert overwritten["done"], "the test did not exercise the overwrite it exists for"
    survivor = [c for (_s, p), c in provider.files.items() if p == "/work/criterion"]
    assert survivor == [b"the mission's version"], survivor


# -- apps-004: the graph, refused at load ----------------------------------- #
def _graph_mission(**overrides):
    """A mission whose tasks are given explicitly, so a test can shape the graph."""
    data = {"name": "chain", "app": WORKER_MANIFEST["name"]}
    data.update(overrides)
    return Mission.load(data)


def _strict_mission(**overrides):
    """The same, with the opt-in gate rule on."""
    return _graph_mission(strict_gates=True, **overrides)


def test_a_dependency_cycle_is_refused_and_the_cycle_is_named():
    with pytest.raises(MissionError) as ei:
        _graph_mission(tasks=[
            {"id": "a", "command": ["true"], "depends_on": ["c"]},
            {"id": "b", "command": ["true"], "depends_on": ["a"]},
            {"id": "c", "command": ["true"], "depends_on": ["b"]},
        ])
    msg = str(ei.value)
    assert "cycle" in msg
    # The cycle itself, not just its existence: an operator with a forty-task
    # mission needs to be told which three tasks to look at.
    assert "a" in msg and "b" in msg and "c" in msg


def test_a_self_edge_is_refused():
    with pytest.raises(MissionError) as ei:
        _graph_mission(tasks=[{"id": "a", "command": ["true"], "depends_on": ["a"]}])
    assert "itself" in str(ei.value) and "'a'" in str(ei.value)


def test_a_dependency_on_an_unknown_task_is_refused_and_named():
    with pytest.raises(MissionError) as ei:
        _graph_mission(tasks=[{"id": "a", "command": ["true"], "depends_on": ["ghost"]}])
    assert "ghost" in str(ei.value)


def test_consuming_an_output_nobody_produces_is_refused():
    with pytest.raises(MissionError) as ei:
        _graph_mission(tasks=[{"id": "a", "command": ["true"], "consumes": {"spec": "/w/s"}}])
    assert "spec" in str(ei.value) and "no task produces" in str(ei.value)


def test_consuming_from_a_task_not_depended_on_is_refused_as_a_race():
    """Produced somewhere, but nothing orders the producer first. Refusing this
    is the difference between a data flow and a race that passes in testing."""
    with pytest.raises(MissionError) as ei:
        _graph_mission(tasks=[
            {"id": "a", "command": ["true"], "produces": {"spec": "/w/spec.md"}},
            {"id": "b", "command": ["true"], "consumes": {"spec": "/w/spec.md"}},
        ])
    msg = str(ei.value)
    assert "does not" in msg and "depend" in msg and "'a'" in msg


def test_a_deep_chain_is_accepted_rather_than_exhausting_the_stack():
    """Mission data comes from outside, so its depth is not ours to trust. A
    RecursionError here would be an error about the interpreter rather than
    about the mission."""
    tasks = [{"id": "t0", "command": ["true"]}]
    tasks += [{"id": f"t{i}", "command": ["true"], "depends_on": [f"t{i-1}"]} for i in range(1, 900)]
    mission = _graph_mission(tasks=tasks)
    assert len(mission.tasks) == 900


# -- apps-004: a check the worker could have written ------------------------ #
def test_a_check_reading_the_workers_own_output_is_refused():
    """The shipped demo's shape: the agent writes both the implementation and
    the test, and the check re-runs the test its own subject authored."""
    with pytest.raises(MissionError) as ei:
        _strict_mission(tasks=[{
            "id": "fizz",
            "prompt": "write fizz.js and fizz.test.js",
            "check": ["node", "/work/fizz.test.js"],
        }])
    msg = str(ei.value)
    assert "/work/fizz.test.js" in msg and "marking its own work" in msg


def test_a_check_reading_a_planted_path_is_accepted():
    mission = _strict_mission(tasks=[{
        "id": "fizz",
        "prompt": "write fizz.js",
        "files": {"/work/fizz.test.js": "require('/work/fizz.js')"},
        "check": ["node", "/work/fizz.test.js"],
    }])
    assert mission.tasks[0].fixed_paths() == {"/work/fizz.test.js"}


def test_a_check_reading_a_consumed_path_is_accepted():
    mission = _strict_mission(tasks=[
        {"id": "spec", "command": ["true"], "produces": {"suite": "/work/suite.js"}},
        {"id": "impl", "command": ["true"], "depends_on": ["spec"],
         "consumes": {"suite": "/work/suite.js"}, "check": ["node", "/work/suite.js"]},
    ])
    assert mission.by_id()["impl"].fixed_paths() == {"/work/suite.js"}


def test_the_check_program_itself_is_not_the_subject():
    """argv[0] comes from the image, not the workspace. Refusing an absolute
    interpreter path would break every check that names one."""
    mission = _strict_mission(tasks=[{
        "id": "t", "command": ["true"],
        "files": {"/work/t.py": "assert True"},
        "check": ["/usr/local/bin/python", "/work/t.py"],
    }])
    assert mission.tasks[0].check[0] == "/usr/local/bin/python"


def test_a_check_naming_no_path_is_left_alone():
    """`check: ["true"]` forges nothing. Existing missions must keep loading."""
    mission = _strict_mission(tasks=[{"id": "t", "command": ["true"], "check": ["true"]}])
    assert mission.tasks[0].check == ["true"]


def test_planted_and_checked_paths_compare_after_normalisation():
    mission = _strict_mission(tasks=[{
        "id": "t", "command": ["true"],
        "files": {"/work/t.js": "1"},
        "check": ["node", "/work/./t.js"],
    }])
    assert mission.tasks[0].fixed_paths() == {"/work/t.js"}


def test_the_gate_rule_is_opt_in_so_a_location_argument_is_not_mistaken_for_a_criterion():
    """`git -C /work diff --quiet` names /work as the place to look; the criterion
    is git's own notion of a clean tree. No syntactic rule can tell that from
    `node /work/its-own-test.js`, so an always-on rule refuses sound missions —
    this repo's own missions/example.json among them, which is how the earlier
    always-on version of this rule was caught."""
    mission = _graph_mission(tasks=[{
        "id": "t", "prompt": "fix the typos and commit",
        "check": ["git", "-C", "/work", "diff", "--quiet"],
    }])
    assert mission.strict_gates is False
    with pytest.raises(MissionError):
        _strict_mission(tasks=[{
            "id": "t", "prompt": "fix the typos and commit",
            "check": ["git", "-C", "/work", "diff", "--quiet"],
        }])


def test_a_mission_can_arrive_in_the_environment(monkeypatch):
    """How a coordinator actually receives work in production.

    A session's declared environment reaches the workload process, and an `exec`
    into that session does not inherit it — verified against production, where
    the grant is present in the entrypoint process's environ and absent from an
    exec'd shell. So the coordinator must run *as* the entrypoint to hold its
    credential, and an entrypoint cannot be handed a file nothing has written.
    The environment carries both, set together at session create.
    """
    from barista_app_factory.__main__ import MISSION_ENV, _load_mission

    monkeypatch.setenv(MISSION_ENV, json.dumps({
        "name": "from-env", "app": WORKER_MANIFEST["name"],
        "tasks": [{"id": "a", "command": ["true"]}],
    }))
    assert _load_mission(None).name == "from-env"


def test_a_generic_app_run_maps_to_the_canonical_factory_mission(monkeypatch):
    from barista_app_factory.__main__ import (
        FACTORY_MISSION_MEDIA_TYPE,
        MISSION_ENV,
        _load_mission,
    )
    from barista_app_sdk import APP_RUN_ENV

    mission = {
        "name": "from-app-run",
        "app": WORKER_MANIFEST["name"],
        "tasks": [{"id": "a", "command": ["true"]}],
    }
    monkeypatch.delenv(MISSION_ENV, raising=False)
    monkeypatch.setenv(
        APP_RUN_ENV,
        json.dumps(
            {
                "schema_version": "v1alpha1",
                "name": "from-app-run",
                "app": "factory@0.1.0",
                "operation": "mission",
                "input": {"media_type": FACTORY_MISSION_MEDIA_TYPE, "value": mission},
            }
        ),
    )

    loaded = _load_mission(None)
    assert loaded.name == "from-app-run"
    assert json.loads(os.environ[MISSION_ENV]) == mission


def test_generic_factory_run_reuses_its_owning_session_as_durable_scope(tmp_path):
    from barista_app_sdk import APP_SESSION_ID_ENV, AppRun
    from barista_app_factory.__main__ import FACTORY_MISSION_MEDIA_TYPE

    manifest = json.loads((REPO / "apps" / "factory" / "manifest.json").read_text())
    mission_doc = {
        "name": "owning-scope",
        "app": WORKER_MANIFEST["name"],
        "tasks": [{"id": "a", "command": ["true"]}],
    }
    run = AppRun.parse(
        {
            "schema_version": "v1alpha1",
            "name": "factory-run-owning-scope",
            "app": "factory@0.1.0",
            "operation": "mission",
            "input": {"media_type": FACTORY_MISSION_MEDIA_TYPE, "value": mission_doc},
        }
    )
    provider = MockProvider(name="app-run")
    with BaristaClient(
        Config(endpoint="http://app-run.invalid"), transport=provider.transport()
    ) as client:
        owning, _ = client.launch_app_run(run, manifest)
        launch_env = provider.session_env[owning.id]
        coordinator = Coordinator(
            client,
            Mission.load(mission_doc),
            tmp_path / "state.json",
            coordinator_session_id=launch_env[APP_SESSION_ID_ENV],
        )
        durable_scope = coordinator._ensure_coordinator_session()

    assert durable_scope == owning.id
    assert len(provider.sessions) == 1, "Factory must not create a second coordinator session"


def test_factory_refuses_generic_run_fields_its_mission_operation_does_not_declare(monkeypatch):
    from barista_app_factory.__main__ import (
        FACTORY_MISSION_MEDIA_TYPE,
        MISSION_ENV,
        _load_mission,
    )
    from barista_app_sdk import APP_RUN_ENV

    monkeypatch.delenv(MISSION_ENV, raising=False)
    monkeypatch.setenv(
        APP_RUN_ENV,
        json.dumps(
            {
                "schema_version": "v1alpha1",
                "name": "unsupported-binding",
                "app": "factory@0.1.0",
                "operation": "mission",
                "input": {
                    "media_type": FACTORY_MISSION_MEDIA_TYPE,
                    "value": {
                        "name": "unsupported-binding",
                        "app": WORKER_MANIFEST["name"],
                        "tasks": [{"id": "a", "command": ["true"]}],
                    },
                },
                "bindings": {
                    "workspace": {
                        "kind": "sh.barista.git.repository",
                        "uri": "https://github.com/acme/site.git",
                    }
                },
            }
        ),
    )

    with pytest.raises(SystemExit, match="does not accept bindings"):
        _load_mission(None)
    assert MISSION_ENV not in os.environ


def test_a_named_mission_path_is_never_replaced_by_the_environment(monkeypatch):
    """A path that is given and missing is an error, not a silent fallback to
    whatever the environment holds — otherwise a stale environment runs instead
    of the mission the operator named."""
    from barista_app_factory.__main__ import MISSION_ENV, _load_mission

    monkeypatch.setenv(MISSION_ENV, json.dumps({
        "name": "stale", "app": WORKER_MANIFEST["name"],
        "tasks": [{"id": "a", "command": ["true"]}],
    }))
    with pytest.raises(FileNotFoundError):
        _load_mission("/definitely/not/here.json")


def test_no_mission_anywhere_is_refused_with_a_usable_message(monkeypatch):
    from barista_app_factory.__main__ import MISSION_ENV, _load_mission

    monkeypatch.delenv(MISSION_ENV, raising=False)
    with pytest.raises(SystemExit) as ei:
        _load_mission(None)
    assert MISSION_ENV in str(ei.value)


def test_no_endpoint_is_reported_as_configuration_not_a_traceback(monkeypatch):
    """The workload still exits non-zero, but its own last report names the
    missing input instead of leaving only GUEST_UNREACHABLE at provider level."""
    from barista_app_factory.__main__ import MISSION_ENV, main

    monkeypatch.setenv(MISSION_ENV, json.dumps({
        "name": "missing-endpoint", "app": WORKER_MANIFEST["name"],
        "tasks": [{"id": "a", "command": ["true"]}],
    }))
    monkeypatch.delenv("BARISTA_HOST_API_ENDPOINT", raising=False)
    with pytest.raises(SystemExit) as exc:
        main(["run"])

    assert exc.value.code != 0
    assert "BARISTA_HOST_API_ENDPOINT" in str(exc.value)
    assert "configuration error" in str(exc.value)


def test_the_manifest_declares_the_authority_the_coordinator_actually_uses():
    """The manifest and the code must agree about scope, and they did not.

    Factory registers every receipt on the `<mission>-coordinator` session it
    *creates* (`_harvest_then_reap` -> `register_artifact(coord, ...)`), while the
    manifest declared `artifact.write` for the app's own session only. Refused
    403 in production the first time a provider actually enforced scopes — it
    could never have worked, and nothing had checked because nothing enforced.

    Asserted as a pair: every scope the coordinator writes at must be declared.
    A test that only listed the manifest's contents would pass just as happily
    after the next divergence.
    """
    manifest = json.loads((REPO / "apps" / "factory" / "manifest.json").read_text())
    declared = {
        (a["action"], a["scope"]) if isinstance(a, dict) else (a, "own_session")
        for a in manifest["permissions"]["actions"]
    }
    # The coordinator session is created by the coordinator, so writing a receipt
    # there is a write at `created_sessions` scope.
    assert ("artifact.write", "created_sessions") in declared
    # And it still writes to its own session, so both scopes are required.
    assert ("artifact.write", "own_session") in declared


def test_the_mission_schema_travels_with_the_package():
    """The schema must sit *inside* the package, or the wheel does not carry it.

    It used to live one directory up. Every source checkout worked and every
    installed copy raised FileNotFoundError on the first mission it validated,
    so the app could be tested but never shipped — found by building the image
    `manifest.json` had named since the app was written. Asserted on the
    package's own directory rather than on the repo layout, because the repo
    layout is exactly what hid the bug.
    """
    import barista_app_factory

    pkg = Path(barista_app_factory.__file__).resolve().parent
    assert (pkg / "mission.schema.json").is_file()
    # And the manifest points at where it actually is, so a reader following the
    # metadata finds the file.
    manifest = json.loads((REPO / "apps" / "factory" / "manifest.json").read_text())
    declared = manifest["metadata"]["sh.barista.factory"]["mission_schema"]
    assert (REPO / "apps" / "factory" / declared).is_file(), declared


def test_the_staged_example_mission_loads_under_strict_gates():
    """The mission that demonstrates the chain is also the proof the pieces
    compose: every stage consumes what the one before it produced, and every
    check reads a planted criterion, under the strictest setting."""
    mission = Mission.load_file(REPO / "apps" / "factory" / "missions" / "staged.json")
    assert mission.strict_gates is True
    by_id = mission.by_id()
    assert by_id["implement"].depends_on == ["spec"]
    assert by_id["implement"].consumes["spec"] == "/work/spec.md"
    # The acceptance suite is planted, so the task it judges cannot have written it.
    assert "/work/acceptance.test.js" in by_id["implement"].files
    assert by_id["implement"].check == ["node", "/work/acceptance.test.js"]


def test_the_shipped_example_mission_still_loads():
    """The repo's own example is the regression guard for over-strictness."""
    mission = Mission.load_file(REPO / "apps" / "factory" / "missions" / "example.json")
    assert [t.id for t in mission.tasks] == ["t1", "t2"]


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


def test_manifest_worker_authority_matches_the_code_that_derives_it():
    """`grants.py` narrows a worker's authority in the coordinator; the manifest
    declares the same narrowing so the *provider* enforces it (design D2). The
    two must not drift — a manifest saying one thing while the code says another
    is how the ratified scenario ended up with nothing behind it.
    """
    sys.path.insert(0, str(REPO / "contracts" / "app-manifest" / "v1alpha1"))
    import rules  # noqa: PLC0415

    permissions = json.loads((REPO / "apps" / "factory" / "manifest.json").read_text())["permissions"]
    child = permissions["child_sessions"]

    worker = rules.normalize(child["actions"])
    assert tuple(g.action for g in worker) == WORKER_ACTIONS
    assert all(g.scope == "own_session" for g in worker), "a worker acts on itself, nothing else"

    # The ratified scenario, both halves: the coordinator may create sessions,
    # a worker may not, and no descendant policy is left to be inferred.
    coordinator = {g.action for g in rules.normalize(permissions["actions"])}
    assert "session.create" in coordinator
    assert "session.create" not in {g.action for g in worker}
    assert child["allow_descendants"] is False

    # And the manifest itself must be semantically clean, not merely well-shaped.
    assert not rules.check_manifest(
        json.loads((REPO / "apps" / "factory" / "manifest.json").read_text())
    )


def test_coordinator_authenticates_with_a_delegated_grant(monkeypatch):
    """The coordinator gets a grant:// credential in the env var the SDK reads —
    not a tenant key. A grant, not a key, is what keeps the provider the only
    minter: the coordinator holds authority it cannot widen or pass on."""
    secrets = json.loads((REPO / "apps" / "factory" / "manifest.json").read_text())[
        "permissions"
    ]["secrets"]
    refs = {s["name"]: s["ref"] for s in secrets}
    assert refs["BARISTA_HOST_API_TOKEN"].startswith("grant://"), refs
    assert refs["NOTIFY_TOKEN"].startswith("secret://"), "a secret is not a grant"

    # The declared name is wired to the client, not decorative: this is the
    # variable Config.from_env() reads, so a provider that resolves the
    # grant:// ref into it has authenticated the coordinator.
    monkeypatch.setenv("BARISTA_HOST_API_ENDPOINT", "http://127.0.0.1:1")
    monkeypatch.setenv("BARISTA_HOST_API_TOKEN", "resolved-delegated-grant")
    assert Config.from_env().resolved_token() == "resolved-delegated-grant"


def test_recovered_running_task_reuses_attempt_no_duplicate_worker(tmp_path):
    """A task persisted as 'running' (mid-flight crash) must re-ensure the SAME
    worker on recovery, not bump the attempt and orphan a duplicate (finding 8).
    """
    from barista_local_provider import create_local_app

    app, store, node = create_local_app(tmp_path / "data")
    port = _free_port()
    try:
        with _Server(app, port):
            with BaristaClient(Config(endpoint=f"http://127.0.0.1:{port}")) as client:
                _install_worker_app(client)
                mission = _mission(tmp_path, n=1)
                coord = Coordinator(client, mission, tmp_path / "state.json")
                coord._ensure_coordinator_session()

                task = mission.tasks[0]
                # Simulate a crash after the first attempt was accepted and saved.
                ts = coord.state.tasks[task.id]
                ts.state, ts.attempts, ts.worker = "running", 1, f"{mission.name}-{task.id}"
                client.ensure_session(
                    mission.app, name=ts.worker,
                    idempotency_key=f"{mission.name}:{task.id}:attempt-1",
                )
                coord.state.save()

                coord.run_task(task)

                assert ts.attempts == 1, "recovery must not start a new attempt"
                assert ts.state == "ok"
                # The one worker was reused and reaped — no orphan left behind.
                remaining = [s for s in client.list_sessions() if s.name == ts.worker]
                assert remaining == [], f"a duplicate worker was orphaned: {remaining}"
    finally:
        store.close()
        node.close()


# --------------------------------------------------------------------------- #
# A mission that outlives its credential (apps-003 §3)
#
# The coordinator's grant is minted once and injected as an environment variable
# that cannot be rewritten in a running process. The reference provider's lives
# fifteen minutes; a mission's default task timeout is 3600 seconds. These prove
# the coordinator refreshes before its credential lapses, and that when it lapses
# anyway the mission says "I lost my authority" rather than "the work failed".
#
# The provider's grant lifetime is SHORTENED rather than the test lengthened, and
# the clock is injected, so nothing here sleeps for a grant.
# --------------------------------------------------------------------------- #
FACTORY_MANIFEST = json.loads((REPO / "apps" / "factory" / "manifest.json").read_text())


class _Clock:
    """A clock the test moves. The provider stamps `expires_at` from it and the
    coordinator reads 'now' from it, so both agree on when a grant dies."""

    def __init__(self, start: float = 1_700_000_000.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _grant_provider(lifetime: float, clock: _Clock, **kw):
    provider = MockProvider(
        name="grant-cloud",
        capabilities=["grants.delegated"],
        child_authority=True,
        grant_lifetime_seconds=lifetime,
        now=clock,
        **kw,
    )
    probe = provider.provision_delegated_probe(FACTORY_MANIFEST)
    return provider, probe


def _coordinator_client(provider, probe):
    """A client authenticated as the coordinator's delegated grant — not a tenant
    key. Only a grant has anything to refresh."""
    return BaristaClient(
        Config(endpoint="http://grant-cloud.invalid", token=probe["coordinator_token"]),
        transport=provider.transport(),
    )


def _factory_mission(n=3, **overrides):
    data = {
        "name": "long-haul",
        "app": FACTORY_MANIFEST["name"],
        "concurrency": 1,
        "tasks": [
            {"id": f"t{i}", "command": ["sh", "-c", f"echo task-{i}"]} for i in range(1, n + 1)
        ],
    }
    data.update(overrides)
    return Mission.load(data)


def test_refresh_margin_is_a_fifth_of_the_lifetime_with_a_floor_and_a_ceiling():
    """Task 3.1 asks for the margin to be chosen deliberately and recorded. The
    number is asserted here so it cannot drift silently."""
    from barista_app_factory.credential import refresh_margin_seconds

    # The reference provider's fifteen minutes: three minutes of room.
    assert refresh_margin_seconds(900.0) == 180.0
    # The floor holds where a fifth would be less than a minute — the coordinator
    # compares its own clock against the provider's timestamp.
    assert refresh_margin_seconds(300.0) == 60.0
    # ...but never past half the lifetime, or a deliberately short-lived grant
    # would be refreshed the instant it arrived, over and over.
    assert refresh_margin_seconds(30.0) == 15.0
    for lifetime in (1.0, 30.0, 300.0, 900.0, 86400.0):
        assert 0 < refresh_margin_seconds(lifetime) <= lifetime / 2


def test_mission_spanning_several_grant_lifetimes_completes(tmp_path):
    """Task 3.3. Each task consumes most of a grant lifetime, so a three-task
    mission outlives two of them; the coordinator must still be acting at the
    end, with no operator supplying anything."""
    clock = _Clock()
    lifetime = 900.0
    provider, probe = _grant_provider(lifetime, clock)
    first_secret = probe["coordinator_token"]

    class TimePasses(Coordinator):
        def run_task(self, task):
            clock.advance(lifetime * 0.9)  # the work takes most of a grant's life
            super().run_task(task)

    with _coordinator_client(provider, probe) as client:
        keeper = CredentialKeeper(client, now=clock, check_interval_seconds=60)
        mission = _factory_mission()
        state = TimePasses(client, mission, tmp_path / "state.json", credential=keeper).run()

    assert state.state == "done", state.authority_lost
    assert state.summary() == {"total": 3, "ok": 3, "failed": 0, "pending": 0}
    # It really did span more than one lifetime, and really did rotate: one
    # refresh to learn the expiry, then one per lifetime crossed.
    assert clock.t - 1_700_000_000.0 > lifetime * 2
    assert keeper.refreshes >= 3, keeper.status()
    assert keeper.active and keeper.lost_authority is None
    assert state.credential["refreshes"] == keeper.refreshes
    assert state.credential["margin_seconds"] == 180.0
    # And the secret the session was born with is dead: rotation, not extension.
    assert first_secret not in provider.principals


def test_the_ticker_refreshes_during_a_single_long_call():
    """A task with an hour's timeout outlives a fifteen-minute grant without the
    coordinator ever reaching a task boundary, so freshness cannot be a per-task
    check alone. Real clock, tiny provider lifetime."""
    provider = MockProvider(
        name="grant-cloud",
        capabilities=["grants.delegated"],
        child_authority=True,
        grant_lifetime_seconds=0.3,
    )
    probe = provider.provision_delegated_probe(FACTORY_MANIFEST)
    with _coordinator_client(provider, probe) as client:
        keeper = CredentialKeeper(client, margin_seconds=0.2, check_interval_seconds=0.01)
        with keeper.running():
            time.sleep(0.6)  # a "long call": nothing calls ensure_fresh
            refreshes = keeper.refreshes
            lost = keeper.lost_authority
    assert lost is None, lost
    assert refreshes >= 3, refreshes  # establish + at least two ticks


def test_ticker_persists_refresh_evidence_before_the_mission_finishes(tmp_path):
    """A managed workload exits with its session, so a status written only after
    `run()` returns cannot be observed. Every ticker rotation must reach the
    durable state while work is still in flight."""
    provider = MockProvider(
        name="grant-cloud",
        capabilities=["grants.delegated"],
        child_authority=True,
        grant_lifetime_seconds=0.3,
    )
    probe = provider.provision_delegated_probe(FACTORY_MANIFEST)
    state_path = tmp_path / "state.json"
    with _coordinator_client(provider, probe) as client:
        keeper = CredentialKeeper(client, margin_seconds=0.2, check_interval_seconds=0.01)
        Coordinator(client, _factory_mission(n=1), state_path, credential=keeper)
        with keeper.running():
            time.sleep(0.6)
            persisted = json.loads(state_path.read_text())

    assert persisted["state"] == "running"
    assert persisted["credential"]["active"] is True
    assert persisted["credential"]["refreshes"] >= 3
    assert persisted["credential"]["inactive_reason"] is None


def test_a_lapsed_credential_is_reported_as_lost_authority_not_failed_work(tmp_path):
    """Task 3.2. The coordinator pauses longer than a whole grant lifetime, so
    the credential lapses before it could be refreshed — a lapsed grant cannot be
    refreshed, by design. What the mission result says about that is the point.
    """
    clock = _Clock()
    lifetime = 900.0
    provider, probe = _grant_provider(lifetime, clock)

    class LongPause(Coordinator):
        def run_task(self, task):
            clock.advance(lifetime * 2)  # nobody refreshed; the grant is gone
            super().run_task(task)

    with _coordinator_client(provider, probe) as client:
        keeper = CredentialKeeper(client, now=clock, check_interval_seconds=60)
        mission = _factory_mission()
        state = LongPause(client, mission, tmp_path / "state.json", credential=keeper).run()

    # Lost authority, named as such, with the operator's next step in it.
    assert state.state == "lost_authority"
    assert state.authority_lost and "expired before it was refreshed" in state.authority_lost
    assert "provisioned" in state.authority_lost

    # And NOT reported as failed work. Nothing was learned about any task.
    summary = state.summary()
    assert summary["failed"] == 0, state.to_dict()["tasks"]
    assert summary["ok"] == 0
    assert summary["pending"] == 3
    assert all(ts.state == "pending" for ts in state.tasks.values())
    assert all(ts.receipt is None for ts in state.tasks.values())

    # The distinction reaches the process exit code an operator sees.
    from barista_app_factory.__main__ import EXIT_LOST_AUTHORITY, exit_code_for

    assert exit_code_for(state) == EXIT_LOST_AUTHORITY == 3


def test_a_refused_action_is_still_failed_work_not_lost_authority(tmp_path):
    """The other side of the same distinction, and the one easy to get wrong: a
    live credential refused an action is a permissions problem in the mission —
    a task failure — while a credential the provider will not accept at all is
    lost authority. 403 and 401 do not mean the same thing."""
    clock = _Clock()
    provider, probe = _grant_provider(900.0, clock)

    with _coordinator_client(provider, probe) as client:
        keeper = CredentialKeeper(client, now=clock, check_interval_seconds=60)
        mission = _factory_mission(n=1)
        coord = Coordinator(client, mission, tmp_path / "state.json", credential=keeper)

        from barista_app_sdk import errors

        def refuse(_task):
            raise errors.AuthorizationError(
                "the presented grant does not authorize session.exec here",
                code="authorization.action_not_granted",
                status=403,
                error_class="authorization",
            )

        coord.run_task = refuse
        with keeper.running():
            coord._guarded(mission.tasks[0])

    assert coord.state.authority_lost is None
    assert coord.state.tasks["t1"].state == "failed"
    assert "AuthorizationError" in coord.state.tasks["t1"].receipt["error"]


def test_the_keeper_is_inactive_and_harmless_without_the_capability(tmp_path):
    """A provider that does not advertise grants.delegated, or a tenant key with
    nothing to refresh, must leave the mission exactly as it was before any of
    this existed — and record why it did nothing."""
    plain = MockProvider(name="cloud-shaped")  # no capabilities
    with BaristaClient(Config(endpoint="http://cloud.invalid"), transport=plain.transport()) as client:
        client._http.post(
            "/v1alpha1/apps", content=json.dumps(WORKER_MANIFEST),
            headers={"content-type": MANIFEST_MEDIA_TYPE},
        )
        keeper = CredentialKeeper(client)
        mission = _mission(tmp_path)
        state = Coordinator(client, mission, tmp_path / "state.json", credential=keeper).run()

    assert state.state == "done"
    assert state.summary() == {"total": 3, "ok": 3, "failed": 0, "pending": 0}
    assert keeper.active is False
    assert keeper.refreshes == 0
    assert "does not advertise grants.delegated" in keeper.inactive_reason
    assert state.credential["inactive_reason"] == keeper.inactive_reason


def test_factory_typed_result_identity_matches_manifest():
    from barista_app_factory.__main__ import FACTORY_VERSION, FACTORY_WORKLOAD_DIGEST

    manifest = json.loads((REPO / "apps" / "factory" / "manifest.json").read_text())
    assert FACTORY_VERSION == manifest["version"]
    assert FACTORY_WORKLOAD_DIGEST == manifest["workload"]["digest"]


def test_factory_publishes_canonical_result_on_owning_scope(tmp_path, monkeypatch):
    import barista_app_sdk.lifecycle as lifecycle
    from barista_app_factory.__main__ import (
        FACTORY_MISSION_MEDIA_TYPE,
        _publish_typed_result,
    )
    from barista_app_factory.state import MissionState, TaskState
    from barista_app_sdk import APP_SESSION_ID_ENV, AppRun, Artifact

    run = AppRun.parse(
        {
            "schema_version": "v1alpha1",
            "name": "factory-result",
            "app": "factory@0.1.0",
            "operation": "mission",
            "input": {
                "media_type": FACTORY_MISSION_MEDIA_TYPE,
                "value": {"name": "factory-result", "app": "worker", "tasks": []},
            },
        }
    )
    state = MissionState(
        mission="factory-result",
        state="done",
        finished_at="2026-08-28T00:01:00Z",
        tasks={
            "done": TaskState(
                id="done",
                state="ok",
                receipt={"mission": "factory-result", "task": "done", "outcome": "ok"},
            )
        },
    )

    class Client:
        registration = None

        def register_artifact(self, session_id, **fields):
            self.registration = (session_id, fields)
            return Artifact(
                id="result-1",
                name=fields["name"],
                digest=fields["digest"],
                size_bytes=fields["size_bytes"],
                media_type=fields["media_type"],
                created_at="2026-08-28T00:01:00Z",
            )

    client = Client()
    result_path = tmp_path / "app-run-result.json"
    monkeypatch.setattr(lifecycle, "APP_RUN_RESULT_PATH", str(result_path))
    monkeypatch.setenv(APP_SESSION_ID_ENV, "factory-result")

    _publish_typed_result(client, run, state)

    document = json.loads(result_path.read_bytes())
    assert document["state"] == "succeeded"
    assert document["run"] == run.name
    assert document["outputs"]["result"]["digest"].startswith("sha256:")
    assert document["evidence"][0]["metadata"]["task"] == "done"
    assert client.registration[0] == "factory-result"
    assert client.registration[1]["name"] == "app-run-result.json"
