"""Factory against a **managed** provider (apps-005 §4).

The sibling flow in `test_standalone_acceptance.py` proves the open stack runs
with Barista Cloud unreachable. This proves the other half of the portability
claim — that the same app runs against a managed provider — which
`factory-app`'s ratified scenario "same mission runs locally and in Cloud" has
always asserted and nothing has ever exercised.

**It needs a provider, a credential, and network**, so it is the opposite of
standalone and cannot live in that flow. It skips with a stated reason when
`BARISTA_HOST_API_ENDPOINT` is absent, rather than failing: a developer running
the suite offline should not see a red test for infrastructure they were never
asked to have.

Run it:

    BARISTA_HOST_API_ENDPOINT="$(< ~/.config/barista/url)" \\
    BARISTA_HOST_API_TOKEN="$(< ~/.config/barista/key)" \\
    BARISTA_FACTORY_COORDINATOR_IMAGE=127.0.0.1:5000/barista-factory:0.4.2 \\
    BARISTA_FACTORY_COORDINATOR_DIGEST=sha256:44481af3d585... \\
    BARISTA_FACTORY_WORKER_IMAGE=127.0.0.1:5000/barista-factory-worker:v1 \\
    BARISTA_FACTORY_WORKER_DIGEST=sha256:590f81ee1e1e... \\
    uv run pytest tests/test_managed_acceptance.py -q

The real-lifetime renewal case is deliberately excluded from that default run:

    uv run pytest -o addopts='' -m slow \
        tests/test_managed_acceptance.py::test_mission_renews_a_real_grant_under_elapsed_time -q

The images are parameters, not constants: they are whatever the provider under
test can pull. Against the beta fleet those are in the node's loopback registry;
another deployment will have its own, and hardcoding one deployment's digests is
how a fixture stops being portable (see `conformance/README.md` on the probe
workload, which learned this the same way).

What this asserts is deliberately the part that had never been proven: the
coordinator runs **as a session**, creates worker sessions as its children,
survives pause/resume without duplicating an accepted attempt, renews a real
delegated grant under elapsed time, and makes failure evidence durable while
leaving failed workers available for forensics.
"""

from __future__ import annotations

import base64
import json
import os
import time
import uuid

import pytest

BASE = "/v1alpha1"
_MISSING = "no managed provider configured: set BARISTA_HOST_API_ENDPOINT (and a token)"


def _env(name: str) -> str | None:
    v = os.environ.get(name)
    return v.strip() if v else None


def _require_images() -> tuple[dict, dict]:
    """The two workloads, from the environment. Both must be resolvable by the
    provider under test; nothing here can know that for it."""
    missing = [
        n
        for n in (
            "BARISTA_FACTORY_COORDINATOR_IMAGE",
            "BARISTA_FACTORY_COORDINATOR_DIGEST",
            "BARISTA_FACTORY_WORKER_IMAGE",
            "BARISTA_FACTORY_WORKER_DIGEST",
        )
        if not _env(n)
    ]
    if missing:
        pytest.skip(f"no factory images configured: {', '.join(missing)}")
    return (
        {"image": _env("BARISTA_FACTORY_COORDINATOR_IMAGE"), "digest": _env("BARISTA_FACTORY_COORDINATOR_DIGEST")},
        {"image": _env("BARISTA_FACTORY_WORKER_IMAGE"), "digest": _env("BARISTA_FACTORY_WORKER_DIGEST")},
    )


@pytest.fixture
def client():
    endpoint = _env("BARISTA_HOST_API_ENDPOINT")
    if not endpoint:
        pytest.skip(_MISSING)
    httpx = pytest.importorskip("httpx")
    token = _env("BARISTA_HOST_API_TOKEN")
    headers = {"accept": "application/json"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    with httpx.Client(base_url=endpoint.rstrip("/"), headers=headers, timeout=180.0) as c:
        yield c


def _key() -> str:
    return "acc-" + uuid.uuid4().hex


def _install(client, manifest: dict):
    return client.post(
        f"{BASE}/apps",
        content=json.dumps(manifest),
        headers={
            "content-type": "application/vnd.barista.app-manifest.v1alpha1+json",
            "Idempotency-Key": _key(),
        },
    )


def _wait_running(client, sid: str, timeout: float = 180.0) -> str:
    deadline = time.time() + timeout
    state = None
    while time.time() < deadline:
        r = client.get(f"{BASE}/sessions/{sid}")
        if r.status_code != 200:
            return f"unreadable ({r.status_code})"
        state = r.json().get("state")
        if state in ("ready", "running", "failed", "stopped", "error"):
            return state
        time.sleep(2)
    return state or "unknown"


def _events(client, sid: str, limit: int = 300) -> list[dict]:
    r = client.get(f"{BASE}/sessions/{sid}/events", params={"max_events": limit})
    if r.status_code != 200:
        return []
    out = []
    for line in r.text.splitlines():
        if line.startswith("data:"):
            try:
                out.append(json.loads(line[5:]))
            except ValueError:
                pass
    return out


def _delete(client, sid: str) -> None:
    response = client.request(
        "DELETE", f"{BASE}/sessions/{sid}", headers={"Idempotency-Key": _key()}
    )
    if response.status_code == 202:
        _wait_operation(client, response)
    else:
        assert response.status_code == 404, response.text


def _require_capabilities(client, *required: str) -> None:
    caps = client.get(f"{BASE}/discovery").json().get("capabilities", [])
    missing = [cap for cap in required if cap not in caps]
    if missing:
        pytest.skip(f"provider does not advertise {', '.join(missing)} (advertises {caps})")


def _install_factory_apps(client, coord_img: dict, worker_img: dict) -> None:
    worker = {
        "schema_version": "v1alpha1",
        "name": "acc-worker",
        "version": "1.0.0",
        "workload": {
            **worker_img,
            "architectures": ["aarch64", "x86_64"],
            "entrypoint": ["/bin/sh", "-c", "sleep infinity"],
            "working_dir": "/work",
            "readiness": {"type": "none"},
        },
    }
    assert _install(client, worker).status_code in (200, 201)

    # The contract's own child-authority example, with this deployment's image.
    from barista_conformance import cases as C

    coordinator = C._child_authority_manifest()
    coordinator["name"] = "acc-factory"
    coordinator["workload"] = {
        **coord_img,
        "architectures": ["aarch64", "x86_64"],
        "entrypoint": ["/usr/local/bin/barista-factory", "run"],
        "working_dir": "/work",
        "readiness": {"type": "log_line", "log_pattern": "coordinator ready", "timeout_seconds": 60},
    }
    installed = _install(client, coordinator)
    assert installed.status_code in (200, 201), installed.text
    assert installed.headers.get("Barista-Grant-Channel"), (
        "the provider did not report a grant channel, so no credential will reach "
        "the coordinator and it cannot create workers"
    )


def _start_mission(client, mission: dict) -> str:
    # The coordinator names its own session `<mission>-coordinator`, and its
    # delegated grant is scoped to exactly that session.
    sid = f"{mission['name']}-coordinator"
    created = client.post(
        f"{BASE}/sessions",
        json={
            "app": "acc-factory",
            "name": sid,
            "env": {
                "BARISTA_FACTORY_MISSION": json.dumps(mission),
                "BARISTA_HOST_API_ENDPOINT": str(client.base_url),
            },
        },
        headers={"Idempotency-Key": _key()},
    )
    assert created.status_code in (200, 201), created.text
    assert _wait_running(client, sid) in ("ready", "running"), "the coordinator never started"
    return sid


def _children(client, sid: str) -> list[str]:
    return [
        child
        for ev in _events(client, sid)
        if ev.get("type") == "session.child_created"
        for child in [(ev.get("data") or {}).get("child")]
        if child
    ]


def _wait_for_children(client, sid: str, expected: set[str], timeout: float = 180.0) -> list[str]:
    deadline = time.time() + timeout
    children: list[str] = []
    while time.time() < deadline:
        children = _children(client, sid)
        if set(children) >= expected:
            return children
        time.sleep(2)
    pytest.fail(f"children never appeared: expected {sorted(expected)}, saw {children}")


def _artifact_names(client, sid: str) -> set[str]:
    response = client.get(f"{BASE}/sessions/{sid}/artifacts")
    if response.status_code != 200:
        return set()
    return {a.get("name") for a in response.json().get("items", [])}


def _wait_for_artifacts(
    client, sid: str, expected: set[str], timeout: float = 300.0
) -> set[str]:
    deadline = time.time() + timeout
    names: set[str] = set()
    while time.time() < deadline:
        names = _artifact_names(client, sid)
        if names >= expected:
            return names
        time.sleep(3)
    pytest.fail(f"artifacts never appeared: expected {sorted(expected)}, saw {sorted(names)}")


def _wait_operation(client, response, timeout: float = 180.0) -> dict:
    assert response.status_code == 202, response.text
    operation_id = response.json()["id"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        op = client.get(f"{BASE}/operations/{operation_id}")
        assert op.status_code == 200, op.text
        body = op.json()
        if body.get("done"):
            assert not body.get("error"), body
            return body
        time.sleep(1)
    pytest.fail(f"operation {operation_id} did not finish")


def _exec_stdout(client, sid: str, command: list[str], timeout: float = 60.0) -> bytes:
    response = client.post(
        f"{BASE}/sessions/{sid}/exec",
        json={"command": command, "timeout_seconds": int(timeout)},
        headers={"Idempotency-Key": _key()},
    )
    assert response.status_code == 200, response.text
    operation_id = response.json()["operation_id"]
    event_cursor = response.json()["event_cursor"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        op = client.get(f"{BASE}/operations/{operation_id}")
        if op.status_code == 200 and op.json().get("done"):
            assert not op.json().get("error"), op.text
            # stdout events do not carry operation_id; the exclusive cursor from
            # ExecStart bounds this exec, and its exit event closes the range.
            chunks: list[bytes] = []
            for ev in _events(client, sid):
                if (ev.get("cursor") or "") <= event_cursor:
                    continue
                if ev.get("type") == "exec.stdout":
                    chunks.append(base64.b64decode((ev.get("data") or {}).get("chunk", "")))
                if ev.get("type") == "exec.exit" and ev.get("operation_id") == operation_id:
                    break
            return b"".join(chunks)
        time.sleep(1)
    pytest.fail(f"exec {operation_id} did not finish")


def _cleanup(client, sid: str, children: set[str]) -> None:
    for session_id in [*sorted(children), sid]:
        _delete(client, session_id)


def test_a_mission_runs_with_the_coordinator_in_a_session(client):
    coord_img, worker_img = _require_images()
    mission_name = "acc" + uuid.uuid4().hex[:6]
    mission = {
        "name": mission_name,
        "app": "acc-worker",
        "concurrency": 2,
        "task_timeout_s": 300,
        "max_attempts": 1,
        "budget": {"max_workers": 4},
        "tasks": [
            {
                "id": "produce",
                "command": ["sh", "-c", "printf 'made-in-session' > /work/out.txt"],
                "produces": {"artifact": "/work/out.txt"},
            },
            {
                "id": "consume",
                "depends_on": ["produce"],
                "consumes": {"artifact": "/work/in.txt"},
                "command": ["sh", "-c", "cat /work/in.txt > /work/seen.txt"],
                # Judged against planted content the task did not author.
                "check": ["sh", "-c", "grep -q made-in-session /work/in.txt"],
            },
        ],
    }

    _require_capabilities(client, "grants.delegated")
    _install_factory_apps(client, coord_img, worker_img)
    sid = _start_mission(client, mission)
    children: set[str] = set()
    try:
        expected_children = {f"{mission_name}-produce", f"{mission_name}-consume"}
        children.update(_wait_for_children(client, sid, expected_children))
        names = _wait_for_artifacts(
            client, sid, {"receipt-produce.json", "receipt-consume.json", "mission-result.json"}
        )
        assert children >= expected_children
        assert {"receipt-produce.json", "receipt-consume.json", "mission-result.json"} <= names
        artifacts = client.get(f"{BASE}/sessions/{sid}/artifacts").json().get("items", [])
        assert any((a.get("media_type") or "").endswith("factory.receipt+json") for a in artifacts)
    finally:
        children.update(_children(client, sid))
        _cleanup(client, sid, children)


def test_pause_resume_keeps_the_accepted_worker(client):
    coord_img, worker_img = _require_images()
    _require_capabilities(client, "grants.delegated", "session.pause_resume")
    _install_factory_apps(client, coord_img, worker_img)

    mission_name = "pause" + uuid.uuid4().hex[:6]
    mission = {
        "name": mission_name,
        "app": "acc-worker",
        "concurrency": 1,
        "task_timeout_s": 180,
        "tasks": [{"id": "slow", "command": ["sh", "-c", "sleep 35; echo resumed"]}],
    }
    sid = _start_mission(client, mission)
    expected_worker = f"{mission_name}-slow"
    children: set[str] = set()
    try:
        children.update(_wait_for_children(client, sid, {expected_worker}))
        _wait_operation(
            client,
            client.post(f"{BASE}/sessions/{sid}/pause", headers={"Idempotency-Key": _key()}),
        )
        assert client.get(f"{BASE}/sessions/{sid}").json().get("state") == "paused"
        time.sleep(5)
        _wait_operation(
            client,
            client.post(f"{BASE}/sessions/{sid}/resume", headers={"Idempotency-Key": _key()}),
        )
        _wait_for_artifacts(client, sid, {"receipt-slow.json", "mission-result.json"})

        accepted = [child for child in _children(client, sid) if child == expected_worker]
        assert accepted == [expected_worker], (
            f"pause/resume duplicated an already accepted worker: {accepted}"
        )
    finally:
        children.update(_children(client, sid))
        _cleanup(client, sid, children)


def test_failure_paths_are_durable_and_failed_workers_remain(client):
    coord_img, worker_img = _require_images()
    _require_capabilities(client, "grants.delegated")
    _install_factory_apps(client, coord_img, worker_img)

    mission_name = "fail" + uuid.uuid4().hex[:6]
    mission = {
        "name": mission_name,
        "app": "acc-worker",
        "concurrency": 2,
        "max_attempts": 2,
        "task_timeout_s": 180,
        "tasks": [
            {"id": "bad-check", "command": ["true"], "check": ["false"]},
            {"id": "blocked", "command": ["true"], "depends_on": ["bad-check"]},
            {
                "id": "success",
                "command": ["sh", "-c", "printf durable > /work/result.txt"],
                "produces": {"output": "/work/result.txt"},
            },
        ],
    }
    sid = _start_mission(client, mission)
    failed_worker = f"{mission_name}-bad-check"
    successful_worker = f"{mission_name}-success"
    children: set[str] = set()
    try:
        children.update(_wait_for_children(client, sid, {failed_worker, successful_worker}))
        names = _wait_for_artifacts(
            client,
            sid,
            {"receipt-bad-check.json", "receipt-success.json", "success-output", "mission-result.json"},
        )
        assert "receipt-blocked.json" not in names
        assert f"{mission_name}-blocked" not in children
        assert client.get(f"{BASE}/sessions/{failed_worker}").status_code == 200, (
            "the failed worker was not left available for forensics"
        )

        deadline = time.time() + 60
        while time.time() < deadline and client.get(
            f"{BASE}/sessions/{successful_worker}"
        ).status_code == 200:
            time.sleep(1)
        assert client.get(f"{BASE}/sessions/{successful_worker}").status_code == 404, (
            "the successful worker was not reaped after its artifacts became durable"
        )
    finally:
        children.update(_children(client, sid))
        _cleanup(client, sid, children)


@pytest.mark.slow
def test_mission_renews_a_real_grant_under_elapsed_time(client):
    """Intentionally takes ~12 minutes against beta's real 900-second grant."""
    coord_img, worker_img = _require_images()
    _require_capabilities(client, "grants.delegated")
    _install_factory_apps(client, coord_img, worker_img)

    mission_name = "renew" + uuid.uuid4().hex[:6]
    mission = {
        "name": mission_name,
        "app": "acc-worker",
        "concurrency": 1,
        "task_timeout_s": 1000,
        "tasks": [
            {
                "id": "hold",
                "collect": False,
                "command": [
                    "sh",
                    "-c",
                    "while [ ! -f /work/release-renewal ]; do sleep 5; done",
                ],
            }
        ],
    }
    sid = _start_mission(client, mission)
    worker = f"{mission_name}-hold"
    children: set[str] = set()
    observed: dict = {}
    started = time.time()
    try:
        children.update(_wait_for_children(client, sid, {worker}))
        deadline = time.time() + 850
        while time.time() < deadline:
            raw = _exec_stdout(client, sid, ["cat", "/work/mission-state.json"])
            observed = json.loads(raw)
            credential = observed.get("credential") or {}
            if credential.get("refreshes", 0) > 1:
                break
            time.sleep(20)

        credential = observed.get("credential") or {}
        assert credential.get("refreshes", 0) > 1, credential
        assert credential.get("active") is True, credential
        assert credential.get("inactive_reason") is None, credential
        assert observed.get("authority_lost") is None, observed
        assert time.time() - started > 700, "renewal was not demonstrated under real elapsed time"

        _exec_stdout(client, worker, ["sh", "-c", "touch /work/release-renewal"])
        _wait_for_artifacts(
            client, sid, {"receipt-hold.json", "mission-result.json"}, timeout=180
        )
    finally:
        children.update(_children(client, sid))
        _cleanup(client, sid, children)
