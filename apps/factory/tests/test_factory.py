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
        store.close(); node.close()


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
