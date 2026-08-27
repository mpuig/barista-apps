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
    BARISTA_FACTORY_COORDINATOR_IMAGE=127.0.0.1:5000/barista-factory:0.3.0 \\
    BARISTA_FACTORY_COORDINATOR_DIGEST=sha256:029b3195acf9... \\
    BARISTA_FACTORY_WORKER_IMAGE=127.0.0.1:5000/barista-factory-worker:v1 \\
    BARISTA_FACTORY_WORKER_DIGEST=sha256:590f81ee1e1e... \\
    uv run --extra test pytest tests/test_managed_acceptance.py -q

The images are parameters, not constants: they are whatever the provider under
test can pull. Against the beta fleet those are in the node's loopback registry;
another deployment will have its own, and hardcoding one deployment's digests is
how a fixture stops being portable (see `conformance/README.md` on the probe
workload, which learned this the same way).

What this asserts is deliberately the part that has never been proven: the
coordinator runs **as a session**, creates worker sessions as its children, and
leaves receipts as provider-side artifacts that outlive it. Pause/resume
mid-mission, a mission outliving one grant lifetime, and the failure paths are
apps-005 §5-§7 and are not covered here.
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
    client.request("DELETE", f"{BASE}/sessions/{sid}", headers={"Idempotency-Key": _key()})


def test_a_mission_runs_with_the_coordinator_in_a_session(client):
    coord_img, worker_img = _require_images()
    caps = client.get(f"{BASE}/discovery").json().get("capabilities", [])
    if "grants.delegated" not in caps:
        pytest.skip(
            "the provider does not advertise grants.delegated, so the coordinator "
            f"cannot be handed a credential to create workers with (advertises {caps})"
        )

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

    # The coordinator names its own session `<mission>-coordinator`, and its
    # delegated grant is scoped to exactly that session — creating it under any
    # other name yields a grant the provider correctly refuses.
    sid = f"{mission_name}-coordinator"
    created = client.post(
        f"{BASE}/sessions",
        json={
            "app": "acc-factory",
            "name": sid,
            # apps-005 D1: the mission arrives in the environment. Nothing writes
            # /work/mission.json, and session env is delivered with creation.
            "env": {
                "BARISTA_FACTORY_MISSION": json.dumps(mission),
                "BARISTA_HOST_API_ENDPOINT": str(client.base_url),
            },
        },
        headers={"Idempotency-Key": _key()},
    )
    assert created.status_code in (200, 201), created.text

    children: set[str] = set()
    artifacts: list[dict] = []
    try:
        assert _wait_running(client, sid) in ("ready", "running"), "the coordinator never started"

        deadline = time.time() + 300
        while time.time() < deadline:
            for ev in _events(client, sid):
                if ev.get("type") == "session.child_created":
                    child = (ev.get("data") or {}).get("child")
                    if child:
                        children.add(child)
            r = client.get(f"{BASE}/sessions/{sid}/artifacts")
            artifacts = r.json().get("items", []) if r.status_code == 200 else []
            receipts = [a for a in artifacts if "receipt" in (a.get("name") or "")]
            if len(receipts) >= len(mission["tasks"]):
                break
            time.sleep(6)

        names = {a.get("name") for a in artifacts}
        assert children >= {f"{mission_name}-produce", f"{mission_name}-consume"}, (
            f"the coordinator did not create a worker per task: {sorted(children)}"
        )
        # The receipts are the durable evidence, and provider-side by necessity:
        # a coordinator exits when its mission ends, taking /work with it.
        for task in ("produce", "consume"):
            assert f"receipt-{task}.json" in names, f"no receipt for {task}: {sorted(names)}"
        assert any((a.get("media_type") or "").endswith("factory.receipt+json") for a in artifacts)
    finally:
        for s in [sid, *sorted(children)]:
            _delete(client, s)
