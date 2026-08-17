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
    # Part of the case-authoring API: an optional-profile case may skip itself
    # for a reason other than "not advertised" (which the runner handles).
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
