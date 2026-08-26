"""Conformance cases.

Each case proves one observable behavior through the published Host API only.
Core cases must pass for any provider. Optional-profile cases run only when the
provider advertises that profile; when it does, they must pass (a skip cannot
certify an advertised profile — see report.evaluate_conformance).
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from . import schemas
from .client import HostAPIClient, new_idempotency_key
from .config import AcquiredDelegation, DelegatedProbe, ProviderConfig
from .profiles import CORE
from .report import CaseResult, Status

CaseFn = Callable[[HostAPIClient, ProviderConfig, list], "CaseResult | None"]

_REGISTRY: list["Case"] = []


@dataclass
class Case:
    id: str
    profile: str
    fn: CaseFn


def case(case_id: str, profile: str = CORE) -> Callable[[CaseFn], CaseFn]:
    def register(fn: CaseFn) -> CaseFn:
        _REGISTRY.append(Case(id=case_id, profile=profile, fn=fn))
        return fn

    return register


def all_cases() -> list[Case]:
    return list(_REGISTRY)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _no_digest_manifest() -> dict:
    path = (
        schemas._contracts_dir()
        / "app-manifest"
        / "v1alpha1"
        / "invalid"
        / "no-digest.json"
    )
    return json.loads(path.read_text())


def _child_authority_manifest() -> dict:
    """The contract's own example of an app that delegates to its children."""
    path = (
        schemas._contracts_dir()
        / "app-manifest"
        / "v1alpha1"
        / "examples"
        / "factory.json"
    )
    return json.loads(path.read_text())


def _runnable_child_authority_manifest(config) -> dict:
    """The contract's child-authority example, with a workload that can boot.

    These cases have to *run* a coordinator, and the example's digest is a
    documentation placeholder that no provider can resolve — the same reason the
    core cases could not use `minimal.json`. Everything they actually assert
    (the declared actions, the child_sessions block, the grant channel) is still
    the contract's own text, verbatim: only `workload` is replaced, and no case
    makes a claim about it.

    This is narrower than substituting a manifest of our own, which the suite
    refuses to do — the app's *identity and permissions* remain the published
    example, so the provider is still being measured against the contract rather
    than against something the suite invented.
    """
    manifest = _child_authority_manifest()
    probe = config.probe_workload.manifest()["workload"]
    manifest["workload"] = {
        **manifest["workload"],
        **{k: probe[k] for k in ("image", "digest", "architectures", "entrypoint")},
    }
    # The example's readiness is a log line its real image prints; the probe
    # image does not, and waiting for it would time out every case below.
    manifest["workload"]["readiness"] = {"type": "none"}
    return manifest


def _over_delegating_manifest() -> dict:
    """Schema-valid, semantically refused: it hands its children an action it
    does not hold. JSON Schema accepts it; a provider must not."""
    path = (
        schemas._contracts_dir()
        / "app-manifest"
        / "v1alpha1"
        / "semantically-invalid"
        / "child-actions-exceed-app.json"
    )
    return json.loads(path.read_text())


def _ensure_a_session(client: HostAPIClient, config) -> str:
    # A session is an instance of an installed app. Install the probe app first
    # (idempotent by name+version), then ensure a session of it. This matches how
    # a real provider resolves a workload from an installed manifest — which is
    # why the workload is the configurable probe rather than the contract's
    # documentation example, whose placeholder digest no real provider can
    # resolve. See ProbeWorkload.
    manifest = config.probe_workload.manifest()
    client.install_app(manifest, key=new_idempotency_key())
    resp = client.ensure_session({"app": manifest["name"], "name": "conf-" + new_idempotency_key()})
    assert resp.status_code in (200, 201), f"ensure returned {resp.status_code}: {resp.text}"
    body = resp.json()
    schemas.assert_valid(schemas.component_validator("Session"), body, "Session")
    return body["id"]


def _wait_until_running(client: HostAPIClient, sid: str, timeout: float = 120.0) -> str:
    """Block until the session is actually running, and return its state.

    Creating a session is asynchronous: ensure answers with the session in a
    transitional state and the workload arrives later. A case that acts the
    instant ensure returns is therefore testing how fast the provider happens to
    be, not whether it honours the contract — and it fails against a provider
    that really boots a machine while passing against one that fakes it.

    Returns the last observed state either way; a case that needs a running
    session asserts on it, so a provider stuck in a transitional state fails
    loudly here rather than as a confusing refusal further down.
    """
    deadline = time.time() + timeout
    state = None
    while time.time() < deadline:
        resp = client.get_session(sid)
        if resp.status_code != 200:
            return f"unreadable ({resp.status_code})"
        state = resp.json().get("state")
        if state in ("ready", "running", "failed", "terminated"):
            return state
        time.sleep(1.0)
    return state or "unknown"


def ok(case_id: str, profile: str, msg: str = "") -> CaseResult:
    return CaseResult(id=case_id, profile=profile, status=Status.PASSED, message=msg)


def skip(case_id: str, profile: str, msg: str) -> CaseResult:
    # A case may skip itself for a reason other than "not advertised" (which the
    # runner handles) — e.g. it needs a credential the contract cannot hand it.
    # The reason is recorded, and on an advertised profile the skip is still a
    # violation: nothing here can certify what it did not observe.
    return CaseResult(id=case_id, profile=profile, status=Status.SKIPPED, message=msg)


# --------------------------------------------------------------------------- #
# Core profile
# --------------------------------------------------------------------------- #
@case("core.discovery")
def discovery(client, config, advertised):
    resp = client.discovery()
    assert resp.status_code == 200, f"discovery returned {resp.status_code}"
    body = resp.json()
    schemas.assert_valid(schemas.component_validator("Discovery"), body, "Discovery")
    assert body.get("core_profile") is True, "core_profile must be advertised true"
    return ok("core.discovery", CORE, "discovery valid; core profile advertised")


@case("core.manifest_rejection")
def manifest_rejection(client, config, advertised):
    resp = client.install_app(_no_digest_manifest(), key=new_idempotency_key())
    assert resp.status_code >= 400, "a manifest without a digest must be rejected"
    body = resp.json()
    schemas.assert_valid(schemas.component_validator("Error"), body, "Error")
    assert body["class"] in ("invalid_request", "compatibility", "capability"), (
        f"unexpected error class {body['class']}"
    )
    return ok("core.manifest_rejection", CORE, "mutable-tag/no-digest manifest rejected before side effects")


@case("core.ensure_and_get")
def ensure_and_get(client, config, advertised):
    sid = _ensure_a_session(client, config)
    got = client.get_session(sid)
    assert got.status_code == 200, f"get_session returned {got.status_code}"
    # The closed Session schema (additionalProperties: false) is the real leak
    # gate: a node address, credential, or internal id would appear as an
    # unexpected field and fail validation. Substring scanning gave both false
    # positives (a fractional-second timestamp contains '10.') and false
    # negatives (IPv6, /run paths, hostnames), so validation replaces it.
    schemas.assert_valid(schemas.component_validator("Session"), got.json(), "Session")
    client.delete_session(sid, key=new_idempotency_key())
    return ok("core.ensure_and_get", CORE, "session created, read back, schema-valid (no leaked fields)")


@case("core.ensure_idempotent")
def ensure_idempotent(client, config, advertised):
    manifest = config.probe_workload.manifest()
    client.install_app(manifest, key=new_idempotency_key())
    key = new_idempotency_key()
    body = {"app": manifest["name"], "name": "idem-" + key}
    first = client.ensure_session(body, key=key)
    second = client.ensure_session(body, key=key)
    assert first.status_code in (200, 201) and second.status_code in (200, 201)
    id1, id2 = first.json()["id"], second.json()["id"]
    assert id1 == id2, f"idempotent ensure returned two sessions: {id1} != {id2}"
    client.delete_session(id1, key=new_idempotency_key())
    return ok("core.ensure_idempotent", CORE, "replayed ensure returned the same session")


@case("core.exec")
def exec_case(client, config, advertised):
    sid = _ensure_a_session(client, config)
    try:
        resp = client.exec(sid, {"command": ["echo", "hello"]}, key=new_idempotency_key())
        assert resp.status_code == 200, f"exec returned {resp.status_code}"
        handle = resp.json()
        schemas.assert_valid(schemas.component_validator("ExecHandle"), handle, "ExecHandle")
        # Operation must be readable, including to completion.
        op_id = handle["operation_id"]
        deadline = time.time() + 10
        done = False
        while time.time() < deadline:
            op = client.get_operation(op_id)
            assert op.status_code == 200
            schemas.assert_valid(schemas.component_validator("Operation"), op.json(), "Operation")
            if op.json()["done"]:
                done = True
                break
            time.sleep(0.05)
        assert done, "exec operation never completed"
        # The handle's event_cursor is an exclusive resume point BEFORE the exec
        # output: reading from it must surface this command's stdout, not skip it.
        seen = list(client.events(sid, cursor=handle["event_cursor"], max_events=10))
        assert any(e["type"] == "exec.stdout" for e in seen), (
            "reading events from the exec handle's cursor skipped the command's stdout"
        )
        return ok("core.exec", CORE, "exec operation completed and its cursor surfaces stdout")
    finally:
        client.delete_session(sid, key=new_idempotency_key())


@case("core.attach_contract")
def attach_contract(client, config, advertised):
    """Attach is part of the core profile: it must upgrade (101) or return a
    classified error (e.g. 426), never a plain unstructured 404/500."""
    sid = _ensure_a_session(client, config)
    try:
        resp = client.attach(sid, mode="raw")
        if resp.status_code == 101:
            return ok("core.attach_contract", CORE, "attach upgraded to a stream")
        assert resp.status_code in (426, 501), (
            f"attach must upgrade or return a classified 426/501, got {resp.status_code}"
        )
        schemas.assert_valid(schemas.component_validator("Error"), resp.json(), "Error")
        assert resp.json()["class"] in ("capability", "compatibility")
        return ok("core.attach_contract", CORE, "attach returns a classified capability error")
    finally:
        client.delete_session(sid, key=new_idempotency_key())


@case("core.events_cursor")
def events_cursor(client, config, advertised):
    sid = _ensure_a_session(client, config)
    try:
        client.exec(sid, {"command": ["echo", "hi"]}, key=new_idempotency_key())
        events = list(client.events(sid, max_events=5))
        assert events, "no events observed"
        for ev in events:
            schemas.assert_valid(schemas.event_validator(), ev, "Event")
        # Cursors are stable and monotone-usable: resuming from the first cursor
        # must not replay that same event.
        first_cursor = events[0]["cursor"]
        resumed = list(client.events(sid, cursor=first_cursor, max_events=5))
        assert all(e["cursor"] != first_cursor for e in resumed), (
            "resuming from a cursor replayed the acknowledged event"
        )
        return ok("core.events_cursor", CORE, "events carry stable, resumable cursors")
    finally:
        client.delete_session(sid, key=new_idempotency_key())


@case("core.artifacts")
def artifacts(client, config, advertised):
    sid = _ensure_a_session(client, config)
    try:
        digest = "sha256:" + "ab" * 32
        reg = client.register_artifact(
            sid,
            {"name": "result.txt", "digest": digest, "size_bytes": 12, "media_type": "text/plain"},
            key=new_idempotency_key(),
        )
        assert reg.status_code == 201, f"register artifact returned {reg.status_code}"
        schemas.assert_valid(schemas.component_validator("Artifact"), reg.json(), "Artifact")
        listed = client.list_artifacts(sid)
        assert listed.status_code == 200
        schemas.assert_valid(schemas.component_validator("ArtifactList"), listed.json(), "ArtifactList")
        assert any(a["digest"] == digest for a in listed.json()["items"]), "artifact not listed"
        return ok("core.artifacts", CORE, "content-addressed artifact registered and listed")
    finally:
        client.delete_session(sid, key=new_idempotency_key())


@case("core.error_shape")
def error_shape(client, config, advertised):
    resp = client.get_session("does-not-exist-" + new_idempotency_key())
    assert resp.status_code == 404, f"expected 404, got {resp.status_code}"
    schemas.assert_valid(schemas.component_validator("Error"), resp.json(), "Error")
    return ok("core.error_shape", CORE, "missing session returns a classified 404 error")


@case("core.capability_error_not_faked")
def capability_error_not_faked(client, config, advertised):
    """An unadvertised optional profile must return a capability error, not a
    weaker fake success."""
    if "session.pause_resume" in advertised:
        return ok(
            "core.capability_error_not_faked",
            CORE,
            "pause_resume is advertised and supported; covered by its own profile case",
        )
    sid = _ensure_a_session(client, config)
    try:
        resp = client.pause(sid, key=new_idempotency_key())
        assert resp.status_code == 501, f"unsupported pause should be 501, got {resp.status_code}"
        schemas.assert_valid(schemas.component_validator("Error"), resp.json(), "Error")
        assert resp.json()["class"] == "capability"
        return ok(
            "core.capability_error_not_faked",
            CORE,
            "unsupported profile returns a capability error rather than faking it",
        )
    finally:
        client.delete_session(sid, key=new_idempotency_key())


@case("core.cleanup")
def cleanup(client, config, advertised):
    sid = _ensure_a_session(client, config)
    resp = client.delete_session(sid, key=new_idempotency_key())
    assert resp.status_code == 202, f"delete returned {resp.status_code}"
    schemas.assert_valid(schemas.component_validator("Operation"), resp.json(), "Operation")
    # Poll until gone.
    deadline = time.time() + 10
    while time.time() < deadline:
        if client.get_session(sid).status_code == 404:
            return ok("core.cleanup", CORE, "deleted session is no longer addressable")
        time.sleep(0.05)
    raise AssertionError("session still addressable after delete")


# --------------------------------------------------------------------------- #
# Optional profile: session.pause_resume
# --------------------------------------------------------------------------- #
@case("pause_resume.roundtrip", profile="session.pause_resume")
def pause_resume_roundtrip(client, config, advertised):
    sid = _ensure_a_session(client, config)
    try:
        # Pause/resume is a statement about a *running* session. Issuing it while
        # the workload is still arriving tests the provider's boot latency, not
        # the profile.
        state = _wait_until_running(client, sid)
        assert state in ("ready", "running"), f"session never started (state={state})"
        p = client.pause(sid, key=new_idempotency_key())
        assert p.status_code == 202, f"pause returned {p.status_code}: {p.text}"
        schemas.assert_valid(schemas.component_validator("Operation"), p.json(), "Operation")
        r = client.resume(sid, key=new_idempotency_key())
        assert r.status_code == 202, f"resume returned {r.status_code}"
        schemas.assert_valid(schemas.component_validator("Operation"), r.json(), "Operation")
        return ok("pause_resume.roundtrip", "session.pause_resume", "pause then resume accepted")
    finally:
        client.delete_session(sid, key=new_idempotency_key())


# --------------------------------------------------------------------------- #
# Optional profile: grants.delegated — child-session authority
#
# These turn the ratified factory-app requirement ("workers SHALL receive
# narrower delegated grants and SHALL not inherit the coordinator's full
# authority") into observable behaviour. Two need only the ordinary credential.
# Three need to make the *same* request as two different principals, which means
# holding two delegated credentials.
#
# apps-002 could only take those from an operator, so they shipped written and
# permanently skipped. apps-003 closes that: a provider advertising
# `grants.delegated` offers grant refresh, and refresh is the contract's only
# positive proof that a client holds a live delegated grant. So the suite now
# creates its own probe session, reads the credential the provider resolved into
# it (the `grant://` reference the manifest declares, observed through exec and
# the event stream), confirms it by refreshing it, and lets the provider mint a
# child beneath it. Nothing is reached around: every step is a published
# operation, and every credential is one the provider minted.
#
# When any step is impossible the cases still skip, saying which step and why —
# and a skip on an advertised profile is still a violation. The suite refuses to
# certify delegation it could not watch happen.
# --------------------------------------------------------------------------- #
DELEGATED = "grants.delegated"


def _no_credentials(why: str) -> str:
    return (
        f"the suite holds no delegated credential: {why}. It tries to obtain one "
        "the way an app does — create a probe session from the contract's "
        "child-authority example, read the grant the provider resolved into its "
        "environment, and confirm it by refreshing it. Supply "
        "config.delegated_probe (BARISTA_CONFORMANCE_DELEGATED_APP / "
        "_COORDINATOR_TOKEN / _COORDINATOR_SESSION / _WORKER_TOKEN / "
        "_WORKER_SESSION) to run this case from operator-supplied credentials "
        "instead."
    )


# --------------------------------------------------------------------------- #
# Obtaining and holding a delegated credential
# --------------------------------------------------------------------------- #
@dataclass
class _Disposable:
    """A grant the suite may destroy — rotate it, revoke it, let it lapse.

    The shared probe credentials cannot be used for that: the cases that follow
    still need them. So each destructive case takes one more child session under
    the probe coordinator and uses the credential the provider minted for it.
    """

    secret: str
    session_id: str


def _declared_grant_env(manifest: dict) -> Optional[str]:
    """The env var name the manifest asks to have a *grant* resolved into.

    An app learns where its credential arrives from its own manifest; so does
    the suite. This is a declared part of the contract, not a provider detail.
    """
    secrets = (manifest.get("permissions", {}) or {}).get("secrets", []) or []
    for secret in secrets:
        if str(secret.get("ref", "")).startswith("grant://"):
            return secret["name"]
    return None


def _grant_env_name(config: ProviderConfig) -> Optional[str]:
    return config.grant_env_var or _declared_grant_env(_child_authority_manifest())


def _action_ids(actions) -> set[str]:
    """Action ids from a grant's reported action list. A provider may report a
    bare id or an `action@scope` pair; the id is what both forms agree on, and
    what a manifest declares."""
    return {str(a).split("@", 1)[0] for a in actions or ()}


def _parse_iso(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _read_session_env(client: HostAPIClient, session_id: str, name: str) -> Optional[str]:
    """Read one environment variable of a running session, through the contract.

    This is how a client observes what an app receives: the provider resolves a
    `grant://` reference into the session's environment, and exec plus the event
    stream are the published way to see it. No private hook and no privilege the
    caller did not already hold over that session.
    """
    started = client.exec(session_id, {"command": ["printenv", name]}, key=new_idempotency_key())
    if started.status_code != 200:
        return None
    handle = started.json()
    deadline = time.time() + 10
    while time.time() < deadline:
        op = client.get_operation(handle["operation_id"])
        if op.status_code == 200 and op.json().get("done"):
            break
        time.sleep(0.05)
    chunks: list[bytes] = []
    try:
        for ev in client.events(session_id, cursor=handle.get("event_cursor"), max_events=20):
            if ev.get("type") == "exec.stdout":
                chunks.append(base64.b64decode(ev["data"]["chunk"]))
            elif ev.get("type") == "exec.exit":
                break
    except Exception:  # noqa: BLE001 - acquisition is best-effort: a failure here
        return None  # becomes a stated skip reason, never a crash
    value = b"".join(chunks).decode("utf-8", "replace").strip()
    return value or None


def _confirm_delegated(client: HostAPIClient, secret: str) -> tuple[Optional[str], str]:
    """Refresh ``secret`` and return the replacement, or why it is unusable.

    This is the confirmation apps-002 could not make. A string read out of a
    session's environment might be a dead token, or the tenant credential handed
    back; refresh accepts only a live delegated grant, so a 200 here means the
    suite holds delegated authority — and holds it fresh, which is what lets a
    whole suite run finish inside one credential's life.
    """
    with client.as_principal(secret) as candidate:
        resp = candidate.refresh_grant()
    if resp.status_code != 200:
        return None, f"refreshing it returned {resp.status_code}, so it is not a live delegated grant"
    body = resp.json() if resp.content else {}
    replacement = body.get("secret") if isinstance(body, dict) else None
    if not isinstance(replacement, str) or not replacement:
        return None, "refresh returned 200 without a replacement secret"
    return replacement, ""


def _acquire_delegated(client: HostAPIClient, config: ProviderConfig) -> AcquiredDelegation:
    """Stand up a coordinator and a worker, and hold both their credentials."""
    manifest = _runnable_child_authority_manifest(config)
    app = manifest["name"]
    created: list[str] = []
    env_name = _grant_env_name(config)
    if not env_name:
        return AcquiredDelegation(
            None,
            "no manifest declares a grant:// secret, so nothing names the variable a "
            "credential arrives in",
            created,
        )

    installed = client.install_app(manifest, key=new_idempotency_key())
    if installed.status_code not in (200, 201):
        detail = ""
        try:
            body = installed.json()
            detail = f" ({body.get('class')}/{body.get('code')}: {body.get('message')})"
        except Exception:  # noqa: BLE001 - a non-JSON body is its own answer
            detail = f" ({installed.text[:200]})"
        required = [
            c["capability"] for c in (manifest.get("capabilities", {}) or {}).get("required", [])
        ]
        return AcquiredDelegation(
            None,
            f"installing the contract's child-authority example returned "
            f"{installed.status_code}{detail}. It declares required capabilities "
            f"{required}; a provider that does not advertise them cannot install it, and "
            "the suite will not substitute a manifest of its own for the contract's own "
            "example",
            created,
        )

    coord = client.ensure_session(
        {"app": app, "name": "conf-probe-coordinator-" + new_idempotency_key()},
        key=new_idempotency_key(),
    )
    if coord.status_code not in (200, 201):
        return AcquiredDelegation(
            None, f"creating a probe session returned {coord.status_code}", created
        )
    coordinator_session = coord.json()["id"]
    created.append(coordinator_session)

    raw = _read_session_env(client, coordinator_session, env_name)
    if not raw:
        return AcquiredDelegation(
            None,
            f"the provider resolved nothing into {env_name} in the probe session, so no "
            "delegated credential was delivered to read",
            created,
        )
    coordinator_token, why = _confirm_delegated(client, raw)
    if coordinator_token is None:
        return AcquiredDelegation(None, f"the value in {env_name} is unusable: {why}", created)

    with client.as_principal(coordinator_token) as coordinator:
        worker = coordinator.ensure_session(
            {"app": app, "name": "conf-probe-worker-" + new_idempotency_key()},
            key=new_idempotency_key(),
        )
        if worker.status_code not in (200, 201):
            return AcquiredDelegation(
                None,
                f"the probe's own grant could not create a child session "
                f"({worker.status_code}), so there is no child credential to compare with",
                created,
            )
        worker_session = worker.json()["id"]
        created.append(worker_session)
        raw_worker = _read_session_env(coordinator, worker_session, env_name)
    if not raw_worker:
        return AcquiredDelegation(
            None, f"the provider resolved nothing into {env_name} in the child session", created
        )
    if raw_worker == raw:
        return AcquiredDelegation(
            None,
            "the child was handed its parent's own credential, so there are not two "
            "principals to compare (that is itself a delegation failure, reported here "
            "because the cases below cannot run to say so)",
            created,
        )
    worker_token, why = _confirm_delegated(client, raw_worker)
    if worker_token is None:
        return AcquiredDelegation(None, f"the child's credential is unusable: {why}", created)

    # A session neither of them created, for the scope-boundary case. Made with
    # the ordinary credential, so its parent is nobody.
    foreign_session = None
    foreign = client.ensure_session(
        {"app": app, "name": "conf-probe-foreign-" + new_idempotency_key()},
        key=new_idempotency_key(),
    )
    if foreign.status_code in (200, 201):
        foreign_session = foreign.json()["id"]
        created.append(foreign_session)

    probe = DelegatedProbe(
        app=app,
        coordinator_token=coordinator_token,
        coordinator_session_id=coordinator_session,
        worker_token=worker_token,
        worker_session_id=worker_session,
        foreign_session_id=foreign_session,
    )
    return AcquiredDelegation(
        probe,
        "acquired through the published contract: a probe session's credential, "
        "confirmed by refreshing it",
        created,
    )


def _keep_alive(client: HostAPIClient, probe: DelegatedProbe) -> bool:
    """Refresh the suite's own credentials, in place.

    The suite's credentials expire like any other. Refreshing between cases is
    both what keeps a multi-case run inside one grant's life and a standing
    demonstration that nothing caps a refresh chain (design D5). False means even
    refresh was refused, so the credentials must be acquired again rather than
    reported as a refusal.
    """
    rotated = {}
    for field_name in ("coordinator_token", "worker_token"):
        replacement, _ = _confirm_delegated(client, getattr(probe, field_name))
        if replacement is None:
            return False
        rotated[field_name] = replacement
    for field_name, secret in rotated.items():
        setattr(probe, field_name, secret)
    return True


def delegated_credentials(
    client: HostAPIClient, config: ProviderConfig
) -> tuple[Optional[DelegatedProbe], str]:
    """The two delegated credentials the delegation cases need, and how they were
    obtained.

    Operator-supplied credentials win, and are used even if refresh will not
    renew them — that is exactly the apps-002 behaviour and it must not regress.
    Credentials the suite acquired for itself are re-acquired when they can no
    longer be renewed, so a long run reports refusals rather than dead tokens.
    """
    if config.delegated_probe is not None:
        if config.acquired is None:
            config.acquired = AcquiredDelegation(
                config.delegated_probe, "operator-supplied credentials"
            )
        # Best effort: renew what the operator handed over, so a suite run
        # longer than one grant lifetime does not start reporting expiry as
        # refusal. Their own copy stops working — that is what rotation means.
        _keep_alive(client, config.delegated_probe)
        return config.delegated_probe, config.acquired.reason
    if config.acquired is None:
        config.acquired = _acquire_delegated(client, config)
    elif config.acquired.probe is not None and not _keep_alive(client, config.acquired.probe):
        sessions = config.acquired.sessions
        config.acquired = _acquire_delegated(client, config)
        config.acquired.sessions[:0] = sessions
    return config.acquired.probe, config.acquired.reason


def release_delegated(client: HostAPIClient, config: ProviderConfig) -> None:
    """Delete what the acquisition created. The probe sessions are sacrificial:
    the suite rotated the grants their own workloads were given."""
    acquired = config.acquired
    if acquired is None:
        return
    for session_id in reversed(acquired.sessions):
        try:
            client.delete_session(session_id, key=new_idempotency_key())
        except Exception:  # noqa: BLE001 - cleanup must not fail a run
            pass
    acquired.sessions.clear()


def _disposable_grant(
    client: HostAPIClient, config: ProviderConfig, probe: DelegatedProbe
) -> tuple[Optional[_Disposable], str]:
    env_name = _grant_env_name(config)
    if not env_name:
        return None, "nothing names the variable a credential arrives in"
    with client.as_principal(probe.coordinator_token) as coordinator:
        resp = coordinator.ensure_session(
            {"app": probe.app, "name": "conf-probe-throwaway-" + new_idempotency_key()},
            key=new_idempotency_key(),
        )
        if resp.status_code not in (200, 201):
            return None, (
                "the coordinator could not create a child session to take a disposable "
                f"grant from ({resp.status_code})"
            )
        session_id = resp.json()["id"]
        if config.acquired is not None:
            config.acquired.sessions.append(session_id)  # deleted when the run ends
        # Wait for it before reading anything out of it. Exec into a session that
        # is still materialising blocks until the provider gives up, which
        # surfaced as an opaque ReadTimeout on every case that wanted a
        # disposable grant — hiding whatever the real answer would have been.
        state = _wait_until_running(coordinator, session_id)
        if state not in ("ready", "running"):
            return None, f"the coordinator's child session never started (state={state})"
        secret = _read_session_env(coordinator, session_id, env_name)
    if not secret:
        return None, f"the provider resolved nothing into {env_name} in that child session"
    return _Disposable(secret=secret, session_id=session_id), ""


def _no_disposable(why: str) -> str:
    return (
        f"the suite could not obtain a grant it is allowed to destroy: {why}. This case "
        "rotates, revokes or expires the grant it tests, so it will not use the shared "
        "probe credentials the other cases still need."
    )


def _liveness(client: HostAPIClient, session_id: str) -> Optional[str]:
    """An action this credential demonstrably performs right now.

    Every refusal below is measured against it: 'refused after rotation' means
    nothing unless something worked before it, and a dead credential must not be
    mistaken for an enforced boundary.
    """
    if client.exec(session_id, {"command": ["true"]}, key=new_idempotency_key()).status_code == 200:
        return "session.exec"
    if client.get_session(session_id).status_code == 200:
        return "session.get"
    return None


def _perform(client: HostAPIClient, action: str, session_id: str):
    if action == "session.exec":
        return client.exec(session_id, {"command": ["true"]}, key=new_idempotency_key())
    return client.get_session(session_id)


@case("grants.child_authority_manifest_accepted", profile=DELEGATED)
def child_authority_manifest_accepted(client, config, advertised):
    """A provider that mints delegated grants must accept a manifest that
    declares what its children receive — the input it mints them from."""
    manifest = _child_authority_manifest()
    child = manifest["permissions"]["child_sessions"]
    assert child.get("actions"), "fixture drifted: it must declare child actions"
    assert child.get("allow_descendants") is False, "fixture drifted: descendants must be denied"
    resp = client.install_app(manifest, key=new_idempotency_key())
    assert resp.status_code in (200, 201), (
        f"install of a child-authority manifest returned {resp.status_code}: {resp.text}"
    )
    schemas.assert_valid(schemas.component_validator("App"), resp.json(), "App")
    return ok(
        "grants.child_authority_manifest_accepted",
        DELEGATED,
        "manifest declaring child actions and denying descendants installed",
    )


@case("grants.over_delegating_manifest_refused", profile=DELEGATED)
def over_delegating_manifest_refused(client, config, advertised):
    """A child's actions must be a subset of the app's own, refused at install
    and naming the offending action. The schema cannot express the rule, so this
    is the only place it is observable."""
    manifest = _over_delegating_manifest()
    rules = schemas.manifest_rules()
    # Guard the fixture: it must be schema-valid, or this proves nothing about
    # the subset rule — it would just be another malformed-manifest rejection.
    schemas.assert_valid(schemas.manifest_validator(), manifest, "AppManifest")
    violations = rules.check_manifest(manifest)
    assert violations, "fixture drifted: it no longer over-delegates"
    offending = sorted({a for v in violations for a in v.actions})

    resp = client.install_app(manifest, key=new_idempotency_key())
    assert resp.status_code >= 400, (
        "a manifest granting its children more than it holds must be refused at "
        f"install, got {resp.status_code}"
    )
    body = resp.json()
    schemas.assert_valid(schemas.component_validator("Error"), body, "Error")
    assert body["class"] in ("invalid_request", "authorization"), (
        f"unexpected error class {body['class']}"
    )
    named = [a for a in offending if a in json.dumps(body)]
    assert named, (
        f"the refusal must name the offending action(s) {offending}; got {body.get('message')!r}"
    )
    return ok(
        "grants.over_delegating_manifest_refused",
        DELEGATED,
        f"over-delegating manifest refused at install, naming {', '.join(named)}",
    )


@case("grants.worker_cannot_create_descendants", profile=DELEGATED)
def worker_cannot_create_descendants(client, config, advertised):
    """factory-app: 'a worker without child-create permission calls session
    create -> the provider denies it even though the coordinator may create
    workers'. Both halves, or it proves nothing: a provider that denies create
    to everyone would otherwise pass."""
    probe, why = delegated_credentials(client, config)
    if probe is None:
        return skip("grants.worker_cannot_create_descendants", DELEGATED, _no_credentials(why))

    with client.as_principal(probe.worker_token) as worker, client.as_principal(
        probe.coordinator_token
    ) as coordinator:
        key = new_idempotency_key()
        denied = worker.ensure_session({"app": probe.app, "name": "descendant-" + key})
        if denied.status_code < 400:
            # Clean up the session that should never have existed, then fail.
            client.delete_session(denied.json()["id"], key=new_idempotency_key())
            raise AssertionError(
                "a worker whose manifest denies descendants created a session "
                f"({denied.status_code})"
            )
        schemas.assert_valid(schemas.component_validator("Error"), denied.json(), "Error")
        assert denied.json()["class"] == "authorization", (
            f"a denied descendant create must be an authorization error, got {denied.json()['class']}"
        )

        allowed = coordinator.ensure_session(
            {"app": probe.app, "name": "worker-" + key}, key=key
        )
        assert allowed.status_code in (200, 201), (
            "the coordinator's own create must still succeed, otherwise the denial "
            f"above proves nothing; got {allowed.status_code}: {allowed.text}"
        )
        schemas.assert_valid(schemas.component_validator("Session"), allowed.json(), "Session")
        coordinator.delete_session(allowed.json()["id"], key=new_idempotency_key())

    return ok(
        "grants.worker_cannot_create_descendants",
        DELEGATED,
        "worker's session create denied as authorization; coordinator's create succeeded",
    )


@case("grants.child_receives_only_declared_subset", profile=DELEGATED)
def child_receives_only_declared_subset(client, config, advertised):
    """A child receives *only* what the manifest declares for it. An action the
    coordinator holds over that same session, and the child was not given, must
    be refused to the child — as authorization, not as 'not found'."""
    probe, why = delegated_credentials(client, config)
    if probe is None:
        return skip("grants.child_receives_only_declared_subset", DELEGATED, _no_credentials(why))
    if probe.scoped_action != "session.get":
        return skip(
            "grants.child_receives_only_declared_subset",
            DELEGATED,
            f"this case observes the withheld action through session.get; "
            f"probe.scoped_action is {probe.scoped_action!r}",
        )

    with client.as_principal(probe.worker_token) as worker, client.as_principal(
        probe.coordinator_token
    ) as coordinator:
        # The worker credential must actually authenticate, or every refusal
        # below is just a dead token and the case is vacuous.
        liveness = worker.get_session("does-not-exist-" + new_idempotency_key())
        assert liveness.status_code != 401, (
            "the worker credential does not authenticate; a refusal from it would prove nothing"
        )

        held = coordinator.get_session(probe.worker_session_id)
        assert held.status_code == 200, (
            "the coordinator must hold this action over the session it created, "
            f"otherwise the refusal below is meaningless; got {held.status_code}"
        )
        schemas.assert_valid(schemas.component_validator("Session"), held.json(), "Session")

        refused = worker.get_session(probe.worker_session_id)
        assert refused.status_code >= 400, (
            f"the child was not granted {probe.scoped_action} and must be refused, "
            f"got {refused.status_code}"
        )
        schemas.assert_valid(schemas.component_validator("Error"), refused.json(), "Error")
        assert refused.json()["class"] == "authorization", (
            "the session demonstrably exists (the coordinator just read it), so the "
            f"child's refusal must be an authorization error, not {refused.json()['class']}"
        )

    return ok(
        "grants.child_receives_only_declared_subset",
        DELEGATED,
        f"{probe.scoped_action} on the child's own session: allowed to the coordinator, "
        "refused to the child it was not declared for",
    )


@case("grants.authority_stops_at_own_children", profile=DELEGATED)
def authority_stops_at_own_children(client, config, advertised):
    """A 'created_sessions' scope is not a licence over the account. The same
    action the coordinator may perform on a session it created must be refused
    against a session it did not."""
    probe, why = delegated_credentials(client, config)
    if probe is None:
        return skip("grants.authority_stops_at_own_children", DELEGATED, _no_credentials(why))
    if not probe.foreign_session_id:
        return skip(
            "grants.authority_stops_at_own_children",
            DELEGATED,
            "no foreign session configured (BARISTA_CONFORMANCE_FOREIGN_SESSION): "
            "without a live session the coordinator did not create, 'authority stops "
            "at its own children' cannot be distinguished from 'the session is absent'",
        )
    if probe.scoped_action != "session.get":
        return skip(
            "grants.authority_stops_at_own_children",
            DELEGATED,
            f"this case observes the scoped action through session.get; "
            f"probe.scoped_action is {probe.scoped_action!r}",
        )

    # The foreign session must exist, or a refusal is indistinguishable from a
    # 404 for a session that was never there. The ordinary credential proves it.
    exists = client.get_session(probe.foreign_session_id)
    assert exists.status_code == 200, (
        f"the configured foreign session {probe.foreign_session_id} is not readable "
        f"with the ordinary credential ({exists.status_code}); the case cannot distinguish "
        "'refused' from 'absent'"
    )

    with client.as_principal(probe.coordinator_token) as coordinator:
        mine = coordinator.get_session(probe.worker_session_id)
        assert mine.status_code == 200, (
            "the coordinator must be able to act on a session it created, otherwise "
            f"the refusal below proves nothing; got {mine.status_code}"
        )
        theirs = coordinator.get_session(probe.foreign_session_id)
        assert theirs.status_code >= 400, (
            "a 'created_sessions' scope must not reach a session the app did not "
            f"create; got {theirs.status_code}"
        )
        schemas.assert_valid(schemas.component_validator("Error"), theirs.json(), "Error")
        assert theirs.json()["class"] in ("authorization", "terminal"), (
            f"unexpected error class {theirs.json()['class']}"
        )

    return ok(
        "grants.authority_stops_at_own_children",
        DELEGATED,
        "coordinator reached the session it created and was refused on one it did not",
    )


# --------------------------------------------------------------------------- #
# Optional profile: grants.delegated — refreshing a held grant (apps-003)
#
# The security argument for refresh is mechanical: the replacement's scope comes
# from the stored grant, so there is no input a caller could widen with. These
# cases hold that argument to the wire. Each asserts BOTH sides — a provider
# that refuses everything must fail, not pass — and the acquisition above is
# itself a both-sides gate: a provider that denied its own probe session the
# right to create a child never reaches these cases at all.
# --------------------------------------------------------------------------- #
@case("grants.refresh_preserves_exactly_the_presented_scope", profile=DELEGATED)
def refresh_preserves_scope(client, config, advertised):
    """The replacement authorizes exactly what the presented grant did — no
    more, no fewer — and says so consistently across a rotation."""
    cid = "grants.refresh_preserves_exactly_the_presented_scope"
    probe, why = delegated_credentials(client, config)
    if probe is None:
        return skip(cid, DELEGATED, _no_credentials(why))
    grant, why = _disposable_grant(client, config, probe)
    if grant is None:
        return skip(cid, DELEGATED, _no_disposable(why))

    with client.as_principal(grant.secret) as holder:
        first = holder.refresh_grant()
    assert first.status_code == 200, (
        f"refreshing a live delegated grant must succeed, got {first.status_code}: {first.text}"
    )
    body = first.json()
    schemas.assert_valid(schemas.component_validator("RefreshedGrant"), body, "RefreshedGrant")
    assert body["secret"] != grant.secret, (
        "refresh returned the secret it was given: that is an extension of the same "
        "credential, not a rotation"
    )
    reported = _action_ids(body["actions"])
    assert reported, "a grant that authorizes nothing is not a grant"
    expires = _parse_iso(body["expires_at"])
    assert expires is not None, f"expires_at is not an ISO-8601 instant: {body['expires_at']!r}"
    assert expires > datetime.now(timezone.utc), (
        f"the replacement is already expired ({body['expires_at']}), so nothing was renewed"
    )

    # Rotating again must not drift the scope: refresh keeps authority, and
    # 'keeps' has to survive being exercised more than once (design D5).
    with client.as_principal(body["secret"]) as holder:
        second = holder.refresh_grant()
    assert second.status_code == 200, (
        f"a replacement must itself be refreshable, got {second.status_code}"
    )
    again = second.json()
    schemas.assert_valid(schemas.component_validator("RefreshedGrant"), again, "RefreshedGrant")
    assert _action_ids(again["actions"]) == reported, (
        f"the scope drifted across a refresh: {sorted(reported)} became "
        f"{sorted(_action_ids(again['actions']))}"
    )
    assert again["resource"] == body["resource"], (
        f"the resource changed across a refresh: {body['resource']!r} -> {again['resource']!r}"
    )

    # An independent source of truth, when the probe app is the contract's own
    # example: the child authority its manifest declares. Without this, 'no more
    # and no fewer' would only be measured against the provider's own answer.
    manifest = _child_authority_manifest()
    checked_against_manifest = False
    if probe.app == manifest["name"]:
        declared = {
            g.action
            for g in schemas.manifest_rules().normalize(
                manifest["permissions"]["child_sessions"]["actions"]
            )
        }
        assert reported == declared, (
            f"the replacement authorizes {sorted(reported)}, but the manifest declares "
            f"{sorted(declared)} for a child session: refresh changed what the grant may do"
        )
        checked_against_manifest = True

    # And behaviourally, both sides.
    with client.as_principal(again["secret"]) as holder:
        if "session.exec" in reported:
            allowed = holder.exec(
                grant.session_id, {"command": ["true"]}, key=new_idempotency_key()
            )
            assert allowed.status_code == 200, (
                "the replacement must still authorize session.exec on the grant's own "
                f"session, got {allowed.status_code}: authority was lost, not kept"
            )
        if "session.get" not in reported:
            refused = holder.get_session(grant.session_id)
            assert refused.status_code >= 400, (
                "the replacement authorizes session.get, which the presented grant did "
                "not: refresh widened the grant"
            )
            schemas.assert_valid(schemas.component_validator("Error"), refused.json(), "Error")
            assert refused.json()["class"] == "authorization", (
                f"unexpected error class {refused.json()['class']}"
            )

    return ok(
        cid,
        DELEGATED,
        f"replacement authorizes exactly {sorted(reported)}"
        + (" (matching the manifest's declared child authority)" if checked_against_manifest else "")
        + ", unchanged across two rotations",
    )


@case("grants.refresh_rotates_the_previous_secret", profile=DELEGATED)
def refresh_rotates_the_previous_secret(client, config, advertised):
    """The previous secret stops working. Without this, refresh is an expiry
    extension and a leaked secret is worth the session's whole lifetime."""
    cid = "grants.refresh_rotates_the_previous_secret"
    probe, why = delegated_credentials(client, config)
    if probe is None:
        return skip(cid, DELEGATED, _no_credentials(why))
    grant, why = _disposable_grant(client, config, probe)
    if grant is None:
        return skip(cid, DELEGATED, _no_disposable(why))

    with client.as_principal(grant.secret) as holder:
        action = _liveness(holder, grant.session_id)
        if action is None:
            return skip(
                cid,
                DELEGATED,
                "the disposable grant performs neither session.exec nor session.get on its "
                "own session, so there is no action whose refusal after a rotation would "
                "mean anything",
            )
        rotated = holder.refresh_grant()
    assert rotated.status_code == 200, f"refresh returned {rotated.status_code}: {rotated.text}"
    replacement = rotated.json()["secret"]

    with client.as_principal(replacement) as holder:
        live = _perform(holder, action, grant.session_id)
        assert live.status_code == 200, (
            f"the replacement cannot perform {action}, which the grant it replaced could "
            f"({live.status_code}): the rotation lost the authority instead of keeping it"
        )

    with client.as_principal(grant.secret) as stale:
        dead = _perform(stale, action, grant.session_id)
        assert dead.status_code >= 400, (
            f"the previous secret still performs {action} after the refresh: rotation "
            "degenerated into extension, and a leaked secret is now good for the whole "
            "session"
        )
        schemas.assert_valid(schemas.component_validator("Error"), dead.json(), "Error")
        assert dead.json()["class"] in ("authentication", "authorization"), (
            f"unexpected error class {dead.json()['class']}"
        )
        again = stale.refresh_grant()
        assert again.status_code >= 400, (
            "the replaced secret could still be refreshed, so it never stopped being a "
            "live credential"
        )

    return ok(
        cid,
        DELEGATED,
        f"{action} works with the replacement and is refused with the secret it replaced "
        f"({dead.json()['class']}); the replaced secret cannot be refreshed either",
    )


@case("grants.refresh_cannot_widen_authority", profile=DELEGATED)
def refresh_cannot_widen_authority(client, config, advertised):
    """A refresh request carrying a scope of its own changes nothing. The
    contract declares no request body; a provider that reads one has implemented
    the `grant.issue` action this contract deliberately does not have."""
    cid = "grants.refresh_cannot_widen_authority"
    probe, why = delegated_credentials(client, config)
    if probe is None:
        return skip(cid, DELEGATED, _no_credentials(why))
    grant, why = _disposable_grant(client, config, probe)
    if grant is None:
        return skip(cid, DELEGATED, _no_disposable(why))

    with client.as_principal(grant.secret) as holder:
        action = _liveness(holder, grant.session_id)
        baseline = holder.refresh_grant()
    assert baseline.status_code == 200, f"refresh returned {baseline.status_code}"
    base = baseline.json()
    base_actions = _action_ids(base["actions"])

    manifest = _child_authority_manifest()
    asked = sorted(
        {g.action for g in schemas.manifest_rules().normalize(manifest["permissions"]["actions"])}
        | {"session.create", "session.delete"}
    )
    assert not set(asked) <= base_actions, (
        "fixture drifted: the child already holds everything this case asks for, so "
        "asking for it proves nothing"
    )
    escalation = sorted(set(asked) - base_actions)

    with client.as_principal(base["secret"]) as holder:
        widened = holder.refresh_grant({"resource": "*", "actions": asked})

    if widened.status_code >= 400:
        # Refusing an unexpected body outright cannot widen either — but it must
        # be a refusal of the *request*, not a broken endpoint or a lost grant.
        schemas.assert_valid(schemas.component_validator("Error"), widened.json(), "Error")
        assert widened.json()["class"] == "invalid_request", (
            f"a refresh carrying a body must be either ignored or refused as an invalid "
            f"request, got {widened.status_code} / {widened.json()['class']}"
        )
        with client.as_principal(base["secret"]) as holder:
            assert holder.refresh_grant().status_code == 200, (
                "the refused body invalidated the grant, which is neither ignoring it nor "
                "refusing it"
            )
        return ok(
            cid,
            DELEGATED,
            f"a refresh body asking for {escalation} was refused as an invalid request; "
            "the grant is unchanged",
        )

    assert widened.status_code == 200, f"unexpected status {widened.status_code}"
    scoped = widened.json()
    schemas.assert_valid(schemas.component_validator("RefreshedGrant"), scoped, "RefreshedGrant")
    assert _action_ids(scoped["actions"]) == base_actions, (
        f"the request asked for {escalation} and the replacement now authorizes "
        f"{sorted(_action_ids(scoped['actions']))} instead of {sorted(base_actions)}: the "
        "scope came from the request, which is issuance, not refresh"
    )
    assert scoped["resource"] == base["resource"], (
        f"the request named a resource and got it: {scoped['resource']!r} instead of "
        f"{base['resource']!r}"
    )

    with client.as_principal(scoped["secret"]) as holder:
        created = holder.ensure_session(
            {"app": probe.app, "name": "conf-probe-widened-" + new_idempotency_key()},
            key=new_idempotency_key(),
        )
        if created.status_code < 400:
            client.delete_session(created.json()["id"], key=new_idempotency_key())
            raise AssertionError(
                "the refreshed grant created a session, which the grant it replaced could "
                "not: refresh conferred authority rather than keeping it"
            )
        schemas.assert_valid(schemas.component_validator("Error"), created.json(), "Error")
        if action is not None:
            still = _perform(holder, action, grant.session_id)
            assert still.status_code == 200, (
                f"the replacement can no longer perform {action}: the refusal above is a "
                "dead credential, not an enforced boundary"
            )

    return ok(
        cid,
        DELEGATED,
        f"a refresh body asking for {escalation} was ignored: the replacement still "
        f"authorizes exactly {sorted(base_actions)} and is still refused session.create",
    )


@case("grants.refresh_refused_after_revocation", profile=DELEGATED)
def refresh_refused_after_revocation(client, config, advertised):
    """A revoked grant cannot be revived, or revocation is a suggestion. The one
    revocation path a black-box client has is deleting the session the grant is
    bound to (design D5)."""
    cid = "grants.refresh_refused_after_revocation"
    probe, why = delegated_credentials(client, config)
    if probe is None:
        return skip(cid, DELEGATED, _no_credentials(why))
    grant, why = _disposable_grant(client, config, probe)
    if grant is None:
        return skip(cid, DELEGATED, _no_disposable(why))

    # While live, it IS refreshable. Assert it, or "refused after revocation"
    # cannot be told apart from a provider that refuses every refresh.
    with client.as_principal(grant.secret) as holder:
        live = holder.refresh_grant()
    assert live.status_code == 200, (
        f"a live grant must be refreshable, got {live.status_code}: without that half, "
        "the refusal below proves nothing"
    )
    token = live.json()["secret"]

    with client.as_principal(probe.coordinator_token) as coordinator:
        gone = coordinator.delete_session(grant.session_id, key=new_idempotency_key())
    if gone.status_code >= 400:
        gone = client.delete_session(grant.session_id, key=new_idempotency_key())
    if gone.status_code >= 400:
        return skip(
            cid,
            DELEGATED,
            f"the grant's session could not be deleted ({gone.status_code}), and deleting "
            "the session a grant is bound to is the only revocation a client can perform "
            "through the published contract",
        )

    # Deletion may be asynchronous; give revocation a bounded moment to land.
    deadline = time.time() + 10
    after = None
    while time.time() < deadline:
        with client.as_principal(token) as holder:
            after = holder.refresh_grant()
        if after.status_code >= 400:
            break
        time.sleep(0.1)
    assert after is not None and after.status_code >= 400, (
        "a grant whose session was deleted could still be refreshed. Deleting a session "
        "SHALL revoke the grants bound to it (Host API deleteSession): the session is the "
        "only thing that bounds a refresh chain, so without that this credential renews "
        "itself forever with nothing left to end it — and no maximum-lifetime ceiling "
        "catches it, because no single credential ever exceeds one"
    )
    schemas.assert_valid(schemas.component_validator("Error"), after.json(), "Error")
    assert after.json()["class"] in ("authentication", "authorization"), (
        f"unexpected error class {after.json()['class']}"
    )
    return ok(
        cid,
        DELEGATED,
        f"refreshable while live, refused once revoked ({after.json()['class']})",
    )


@case("grants.refresh_refused_after_expiry", profile=DELEGATED)
def refresh_refused_after_expiry(client, config, advertised):
    """An expired grant cannot be revived, or expiry is advisory.

    Expiry is the one half of that requirement no request can produce: it
    happens by the clock. So this case reads the lifetime the provider itself
    reported and waits it out when that is affordable, and otherwise skips
    saying exactly what would make it provable. It does not pass on the strength
    of the revocation case next door.
    """
    cid = "grants.refresh_refused_after_expiry"
    probe, why = delegated_credentials(client, config)
    if probe is None:
        return skip(cid, DELEGATED, _no_credentials(why))
    grant, why = _disposable_grant(client, config, probe)
    if grant is None:
        return skip(cid, DELEGATED, _no_disposable(why))

    with client.as_principal(grant.secret) as holder:
        action = _liveness(holder, grant.session_id)
        live = holder.refresh_grant()
    assert live.status_code == 200, f"a live grant must be refreshable, got {live.status_code}"
    body = live.json()
    token = body["secret"]
    expires = _parse_iso(body["expires_at"])
    assert expires is not None, f"expires_at is not an ISO-8601 instant: {body['expires_at']!r}"

    # expires_at may be truncated to whole seconds, so allow a second of slack
    # plus a second for clock skew between the suite and the provider.
    remaining = (expires - datetime.now(timezone.utc)).total_seconds()
    if remaining > config.expiry_wait_seconds:
        return skip(
            cid,
            DELEGATED,
            f"this provider's delegated grants live about {remaining:.0f}s, and proving "
            f"that an expired one is refused means waiting that long, which exceeds the "
            f"suite's {config.expiry_wait_seconds:.0f}s budget. Run the suite against a "
            "tenant configured with a short grant lifetime, or raise "
            f"BARISTA_CONFORMANCE_EXPIRY_WAIT_SECONDS above {remaining:.0f}. Passing "
            "without observing it would certify expiry on the strength of the revocation "
            "case, which is a different requirement"
        )

    time.sleep(max(0.0, remaining) + 2.0)

    with client.as_principal(token) as holder:
        after = holder.refresh_grant()
        assert after.status_code >= 400, (
            "an expired grant could still be refreshed, so its expiry was advisory and a "
            "leaked secret never stops being renewable"
        )
        schemas.assert_valid(schemas.component_validator("Error"), after.json(), "Error")
        assert after.json()["class"] in ("authentication", "authorization"), (
            f"unexpected error class {after.json()['class']}"
        )
        if action is not None:
            dead = _perform(holder, action, grant.session_id)
            assert dead.status_code >= 400, (
                f"the expired grant can still perform {action}, so it did not expire at "
                "all and the refusal above says nothing about expiry"
            )

    return ok(
        cid,
        DELEGATED,
        f"refreshable while live; after its {remaining:.0f}s lifetime elapsed, refresh was "
        f"refused ({after.json()['class']}) and the grant could no longer act",
    )


@case("grants.refresh_refuses_a_credential_with_nothing_to_refresh", profile=DELEGATED)
def refresh_refuses_a_non_grant(client, config, advertised):
    """Two credentials refresh must refuse, for the same reason from two
    directions: a tenant credential (it holds authority directly — there is no
    grant to rotate, and answering it would make refresh a way to *obtain*
    delegated authority) and a grant bound to no session (there is a grant, but
    nothing to end its chain, so it would renew past any maximum-lifetime
    ceiling in steps that never individually exceed it).

    An unbound grant cannot be produced by a black-box client — every credential
    it can obtain arrives inside a session — so that half is asserted when the
    provider can supply one (`BARISTA_CONFORMANCE_UNBOUND_GRANT`). The case's
    both-sides evidence does not depend on it: the same endpoint is shown
    accepting a session-bound grant below.
    """
    cid = "grants.refresh_refuses_a_credential_with_nothing_to_refresh"
    refused = client.refresh_grant()
    assert refused.status_code >= 400, (
        f"refresh returned {refused.status_code} to the ordinary credential, which holds "
        "no grant: that is an operation for obtaining delegated authority, not for "
        "keeping it"
    )
    schemas.assert_valid(schemas.component_validator("Error"), refused.json(), "Error")
    assert refused.json()["class"] in ("authentication", "authorization"), (
        f"unexpected error class {refused.json()['class']}"
    )

    # Both sides: the same endpoint accepts a real delegated grant.
    probe, why = delegated_credentials(client, config)
    if probe is None:
        return skip(cid, DELEGATED, _no_credentials(why))
    grant, why = _disposable_grant(client, config, probe)
    if grant is None:
        return skip(cid, DELEGATED, _no_disposable(why))
    with client.as_principal(grant.secret) as holder:
        accepted = holder.refresh_grant()
    assert accepted.status_code == 200, (
        f"the same endpoint refused a live delegated grant too ({accepted.status_code}), so "
        "the refusal above is not about having nothing to refresh"
    )

    unbound = "not supplied"
    if config.unbound_grant:
        with client.as_principal(config.unbound_grant) as holder:
            denied = holder.refresh_grant()
            assert denied.status_code >= 400, (
                "a grant bound to no session was refreshed: nothing ends that chain, so "
                "its holder renews past any maximum-lifetime ceiling in steps that never "
                "individually exceed it, and no single observation looks wrong"
            )
            schemas.assert_valid(schemas.component_validator("Error"), denied.json(), "Error")
            assert denied.json()["class"] == "authorization", (
                "an unbound grant authenticates fine — it is refused for what it is, so "
                f"the class must be authorization, not {denied.json()['class']}"
            )
            # A refusal must not strand the caller: the failure direction is
            # rollback, so the presented credential still works afterwards.
            still = holder.refresh_grant()
            assert still.status_code == denied.status_code, (
                "the refusal changed the credential's state: a refused refresh must leave "
                "the presented secret exactly as it was"
            )
        unbound = f"refused ({denied.json()['class']}/{denied.json()['code']})"

    return ok(
        cid,
        DELEGATED,
        f"refused for a credential holding no grant ({refused.json()['class']}), accepted "
        f"for a session-bound grant; unbound grant: {unbound}",
    )
