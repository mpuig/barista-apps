"""Conformance cases.

Each case proves one observable behavior through the published Host API only.
Core cases must pass for any provider. Optional-profile cases run only when the
provider advertises that profile; when it does, they must pass (a skip cannot
certify an advertised profile — see report.evaluate_conformance).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from . import schemas
from .client import HostAPIClient, new_idempotency_key
from .config import ProviderConfig
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
def _minimal_manifest() -> dict:
    path = (
        schemas._contracts_dir()
        / "app-manifest"
        / "v1alpha1"
        / "examples"
        / "minimal.json"
    )
    return json.loads(path.read_text())


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


def _ensure_a_session(client: HostAPIClient) -> str:
    # A session is an instance of an installed app. Install the minimal app
    # first (idempotent by name+version), then ensure a session of it. This
    # matches how a real provider resolves a workload from an installed manifest.
    manifest = _minimal_manifest()
    client.install_app(manifest, key=new_idempotency_key())
    resp = client.ensure_session({"app": manifest["name"], "name": "conf-" + new_idempotency_key()})
    assert resp.status_code in (200, 201), f"ensure returned {resp.status_code}: {resp.text}"
    body = resp.json()
    schemas.assert_valid(schemas.component_validator("Session"), body, "Session")
    return body["id"]


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
    sid = _ensure_a_session(client)
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
    manifest = _minimal_manifest()
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
    sid = _ensure_a_session(client)
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
    sid = _ensure_a_session(client)
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
    sid = _ensure_a_session(client)
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
    sid = _ensure_a_session(client)
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
    sid = _ensure_a_session(client)
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
    sid = _ensure_a_session(client)
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
    sid = _ensure_a_session(client)
    try:
        p = client.pause(sid, key=new_idempotency_key())
        assert p.status_code == 202, f"pause returned {p.status_code}"
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
# authority") into observable behaviour. Two of them need only the ordinary
# credential and run wherever the profile is advertised. Three need to make the
# *same* request as two different principals, and v1alpha1 has no endpoint that
# hands a delegated grant to a client — so they run when an operator supplies
# the credentials the provider minted (config.DelegatedProbe) and otherwise skip
# with that reason. A skip on an advertised profile is a violation, by design:
# the suite refuses to certify delegation it could not watch happen.
# --------------------------------------------------------------------------- #
DELEGATED = "grants.delegated"

_NO_PROBE = (
    "no delegated credentials configured: Host API v1alpha1 has no endpoint that "
    "hands a client a delegated grant, so the suite cannot mint a coordinator or "
    "worker credential itself. Supply config.delegated_probe "
    "(BARISTA_CONFORMANCE_COORDINATOR_TOKEN / _WORKER_TOKEN / _SESSION / "
    "_DELEGATED_APP) to run this case."
)


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
    probe = config.delegated_probe
    if probe is None:
        return skip("grants.worker_cannot_create_descendants", DELEGATED, _NO_PROBE)

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
    probe = config.delegated_probe
    if probe is None:
        return skip("grants.child_receives_only_declared_subset", DELEGATED, _NO_PROBE)
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
    probe = config.delegated_probe
    if probe is None:
        return skip("grants.authority_stops_at_own_children", DELEGATED, _NO_PROBE)
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
