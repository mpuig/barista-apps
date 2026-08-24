"""In-process mock Host API provider — a TEST DOUBLE for exercising the
conformance suite offline. It is intentionally not the real local provider
(that is apps-001 section 3); it exists only so the suite can prove it detects
pass / fail / skip and enforces the no-skip-satisfies-advertised rule.
"""

from __future__ import annotations

import functools
import importlib.util
import json
import re
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import httpx

BASE = "/v1alpha1"
REPO = Path(__file__).resolve().parents[2]


@functools.lru_cache(maxsize=1)
def _manifest_rules():
    """Load the contract's reference implementation of the rules JSON Schema
    cannot express. By path, so this double stays importable from any package's
    tests without the conformance package on sys.path."""
    path = REPO / "contracts" / "app-manifest" / "v1alpha1" / "rules.py"
    spec = importlib.util.spec_from_file_location("mock_manifest_rules", path)
    module = importlib.util.module_from_spec(spec)
    # Register before exec: the module defines dataclasses, and @dataclass
    # resolves annotations through sys.modules[cls.__module__].
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


TENANT = "tenant"


@dataclass
class Principal:
    """Who is calling. The tenant credential holds everything; an app session
    holds exactly the (action, scope) pairs the provider minted for it from the
    manifest — which is the only place a child's authority comes from."""

    kind: str = TENANT
    session_id: Optional[str] = None
    grants: set[tuple[str, str]] = field(default_factory=set)
    may_create_children: bool = True
    created: set[str] = field(default_factory=set)
    wide_scope: bool = False
    """Dishonest mode: treat a 'created_sessions' grant as reaching any session.
    The suite must catch it — that is a licence over the account, not a scope."""

    def holds(self, action: str) -> bool:
        return any(a == action for a, _ in self.grants)

    def may(self, action: str, target: Optional[str]) -> bool:
        if self.kind == TENANT:
            return True
        if action == "session.create":
            return self.may_create_children
        if target is not None and target == self.session_id:
            return (action, "own_session") in self.grants
        if target is not None and target in self.created:
            return (action, "created_sessions") in self.grants
        if self.wide_scope and (action, "created_sessions") in self.grants:
            return True
        return False


class MockProvider:
    def __init__(
        self,
        *,
        name: str = "mock",
        version: str = "0.0.0",
        capabilities: Optional[list[str]] = None,
        fake_unadvertised_pause: bool = False,
        child_authority: bool = False,
        child_inherits_parent_authority: bool = False,
        ignore_created_scope: bool = False,
    ):
        self.name = name
        self.version = version
        self.capabilities = capabilities or []
        self.fake_unadvertised_pause = fake_unadvertised_pause
        self.child_authority = child_authority
        """Implement child-session authority: refuse an over-delegating manifest
        at install, and authorize every request against the caller's minted
        (action, scope) pairs. False reproduces a provider that advertises
        delegated grants without honouring them — which the suite must catch."""
        self.child_inherits_parent_authority = child_inherits_parent_authority
        """Dishonest mode: mint a child the PARENT's actions, ignoring
        child_sessions.actions. Exactly the hole this contract closes."""
        self.ignore_created_scope = ignore_created_scope
        """Dishonest mode: let a 'created_sessions' grant reach any session."""

        self.sessions: dict[str, dict] = {}
        self.idem_sessions: dict[str, str] = {}
        self.operations: dict[str, dict] = {}
        self.artifacts: dict[str, list[dict]] = {}
        self.events: dict[str, list[dict]] = {}
        self.apps: dict[str, dict] = {}
        self.principals: dict[str, Principal] = {}
        self._seq = 0

    # -- helpers ---------------------------------------------------------- #
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _next_cursor(self) -> str:
        self._seq += 1
        return f"{self._seq:012d}"

    def _emit(self, session_id: str, type_: str, data: dict, operation_id: str | None = None) -> str:
        cursor = self._next_cursor()
        ev = {
            "cursor": cursor,
            "type": type_,
            "session_id": session_id,
            "time": "2026-08-17T00:00:00Z",
            "data": data,
        }
        if operation_id:
            ev["operation_id"] = operation_id
        self.events.setdefault(session_id, []).append(ev)
        return cursor

    @staticmethod
    def _json(status: int, body: dict) -> httpx.Response:
        return httpx.Response(status, json=body)

    @staticmethod
    def _error(status: int, cls: str, code: str, message: str) -> httpx.Response:
        return httpx.Response(
            status,
            json={"class": cls, "code": code, "message": message, "retryable": False},
        )

    # -- principals and delegated grants ---------------------------------- #
    def _principal(self, request: httpx.Request) -> Optional[Principal]:
        """Resolve the caller. None means 'presented a credential I do not know'."""
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return Principal(kind=TENANT)
        token = auth[len("bearer ") :].strip()
        if not self.child_authority:
            return Principal(kind=TENANT)
        return self.principals.get(token)

    def _mint(self, manifest: dict, session_id: str, *, child: bool) -> str:
        """Mint a grant from the manifest — the provider is the only minter.

        The parent's own actions when child is False; ``child_sessions.actions``
        when True. Nothing here can be asked for by the holder of another grant.
        """
        rules = _manifest_rules()
        permissions = manifest.get("permissions", {}) or {}
        child_block = permissions.get("child_sessions", {}) or {}
        declared = child_block.get("actions") if child else permissions.get("actions")
        if child and self.child_inherits_parent_authority:
            declared = permissions.get("actions")
        grants = {(g.action, g.scope) for g in rules.normalize(declared)}
        may_create = ("session.create", "own_session") in grants
        if child and not self.child_inherits_parent_authority:
            # A child creates descendants only when the manifest says so — the
            # ratified factory-app scenario, enforced at the provider.
            may_create = may_create and bool(child_block.get("allow_descendants", False))
        token = ("child-" if child else "app-") + uuid.uuid4().hex[:12]
        self.principals[token] = Principal(
            kind="app",
            session_id=session_id,
            grants=grants,
            may_create_children=may_create,
            wide_scope=self.ignore_created_scope,
        )
        return token

    def provision_delegated_probe(self, manifest: dict) -> dict:
        """TEST FIXTURE ONLY — plays the *operator*, not the suite.

        Installs a child-authority manifest, creates a coordinator session and
        one worker beneath it, and hands back the credentials this provider
        minted for each, plus a session neither of them created. The conformance
        suite never calls this: it receives the same values through
        ``ProviderConfig.delegated_probe``, exactly as an operator would supply
        them. Host API v1alpha1 has no endpoint that would let the suite obtain
        a delegated grant itself.
        """
        assert self.child_authority, "a provider that does not honour child authority cannot provision this"
        name = manifest["name"]
        self.apps[name] = manifest

        coordinator_sid = self._new_session(name, f"{name}-coordinator")
        coordinator_token = self._mint(manifest, coordinator_sid, child=False)
        coordinator = self.principals[coordinator_token]

        worker_sid = self._new_session(name, f"{name}-worker", parent=coordinator_sid)
        coordinator.created.add(worker_sid)
        worker_token = self._mint(manifest, worker_sid, child=True)

        foreign_sid = self._new_session(name, "unrelated-session")

        return {
            "app": name,
            "coordinator_token": coordinator_token,
            "coordinator_session_id": coordinator_sid,
            "worker_token": worker_token,
            "worker_session_id": worker_sid,
            "foreign_session_id": foreign_sid,
        }

    def _new_session(self, app: str, name: Optional[str], parent: Optional[str] = None) -> str:
        sid = "sess-" + uuid.uuid4().hex[:12]
        session = {
            "id": sid,
            "app": app,
            "state": "running",
            "created_at": "2026-08-17T00:00:00Z",
        }
        if name:
            session["name"] = name
        if parent:
            session["lineage"] = {"parent_session_id": parent}
        self.sessions[sid] = session
        self.artifacts[sid] = []
        self._emit(sid, "session.state_changed", {"state": "running"})
        return sid

    def _forbid(self, action: str) -> httpx.Response:
        return self._error(
            403,
            "authorization",
            "authorization.action_not_granted",
            f"the presented grant does not authorize {action} on this resource",
        )

    # -- router ----------------------------------------------------------- #
    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        idem = request.headers.get("Idempotency-Key")

        principal = self._principal(request)
        if principal is None:
            return self._error(
                401, "authentication", "authentication.unknown_credential", "unknown credential"
            )

        if path == f"{BASE}/discovery" and method == "GET":
            return self._json(
                200,
                {
                    "contract_versions": ["v1alpha1"],
                    "provider": {"name": self.name, "version": self.version},
                    "core_profile": True,
                    "capabilities": self.capabilities,
                    "limits": {"max_concurrent_sessions": 100},
                },
            )

        if path == f"{BASE}/apps" and method == "POST":
            manifest = json.loads(request.content)
            if "digest" not in manifest.get("workload", {}):
                return self._error(
                    422, "invalid_request", "manifest.no_digest",
                    "workload.digest is required; a mutable tag is not identity",
                )
            if self.child_authority:
                # The subset rule is not in the schema, so it is checked here —
                # at install, before any side effect, naming what exceeded what.
                violations = _manifest_rules().check_manifest(manifest)
                if violations:
                    detail = "; ".join(str(v) for v in violations)
                    return self._error(
                        422,
                        "invalid_request",
                        "manifest.child_authority_exceeds_app",
                        f"refusing to install {manifest['name']}: {detail}",
                    )
            self.apps[manifest["name"]] = manifest
            return self._json(
                201,
                {
                    "name": manifest["name"],
                    "version": manifest["version"],
                    "digest": manifest["workload"]["digest"],
                    "granted_capabilities": self.capabilities,
                    "installed_at": "2026-08-17T00:00:00Z",
                },
            )

        if path == f"{BASE}/sessions" and method == "POST":
            body = json.loads(request.content)
            if idem and idem in self.idem_sessions:
                sid = self.idem_sessions[idem]
                return self._json(200, self.sessions[sid])
            if not principal.may("session.create", None):
                return self._forbid("session.create")
            sid = self._new_session(
                body.get("app", "unknown"), body.get("name"), parent=principal.session_id
            )
            session = self.sessions[sid]
            if principal.kind != TENANT:
                principal.created.add(sid)
                manifest = self.apps.get(session["app"])
                if manifest is not None:
                    # The provider mints the child's narrower grant. Nobody can
                    # ask for it through the API — that is the point.
                    self._mint(manifest, sid, child=True)
            if idem:
                self.idem_sessions[idem] = sid
            return self._json(201, session)

        if path == f"{BASE}/sessions" and method == "GET":
            if principal.kind == TENANT:
                return self._json(200, {"items": list(self.sessions.values())})
            if not principal.holds("session.list"):
                return self._forbid("session.list")
            visible = [
                self.sessions[s]
                for s in principal.created
                if s in self.sessions and principal.may("session.list", s)
            ]
            return self._json(200, {"items": visible})

        m = re.match(rf"^{BASE}/sessions/([^/]+)$", path)
        if m:
            sid = m.group(1)
            if method == "GET":
                if not principal.may("session.get", sid):
                    # A caller with no authority over this session learns nothing
                    # about whether it exists beyond "not yours".
                    return self._forbid("session.get")
                if sid not in self.sessions:
                    return self._error(404, "terminal", "session.not_found", "no such session")
                return self._json(200, self.sessions[sid])
            if method == "DELETE":
                if not principal.may("session.delete", sid):
                    return self._forbid("session.delete")
                self.sessions.pop(sid, None)
                op = self._make_op("delete", sid)
                return self._json(202, op)

        m = re.match(rf"^{BASE}/sessions/([^/]+)/(pause|resume)$", path)
        if m and method == "POST":
            sid, verb = m.group(1), m.group(2)
            advertised = "session.pause_resume" in self.capabilities
            if not advertised:
                if self.fake_unadvertised_pause:
                    # Dishonest provider: fakes success. The suite must catch this.
                    return self._json(200, {"ok": True})
                return self._error(
                    501, "capability", "capability.unsupported",
                    "session.pause_resume is not supported by this provider",
                )
            op = self._make_op(verb, sid)
            return self._json(202, op)

        m = re.match(rf"^{BASE}/sessions/([^/]+)/exec$", path)
        if m and method == "POST":
            sid = m.group(1)
            if sid not in self.sessions:
                return self._error(404, "terminal", "session.not_found", "no such session")
            op_id = "op-" + uuid.uuid4().hex[:12]
            # event_cursor is an EXCLUSIVE resume point captured BEFORE the exec
            # events, so reading after it yields this command's stdout onward.
            event_cursor = f"{self._seq:012d}"
            self._emit(sid, "exec.stdout", {"chunk": "aGVsbG8="}, op_id)
            self._emit(sid, "exec.exit", {"exit_code": 0}, op_id)
            self.operations[op_id] = {
                "id": op_id, "kind": "exec", "done": True, "session_id": sid,
                "result": {"exit_code": 0}, "last_event_cursor": event_cursor,
            }
            return self._json(200, {"operation_id": op_id, "event_cursor": event_cursor})

        m = re.match(rf"^{BASE}/sessions/([^/]+)/attach$", path)
        if m and method == "GET":
            sid = m.group(1)
            if sid not in self.sessions:
                return self._error(404, "terminal", "session.not_found", "no such session")
            # This double has no byte stream to upgrade to: return the contract's
            # structured 426 rather than a plain error.
            return self._error(426, "capability", "attach.upgrade_required", "attach not supported")

        m = re.match(rf"^{BASE}/sessions/([^/]+)/artifacts$", path)
        if m:
            sid = m.group(1)
            if method == "POST":
                body = json.loads(request.content)
                art = {
                    "id": "art-" + uuid.uuid4().hex[:12],
                    "name": body["name"],
                    "digest": body["digest"],
                    "size_bytes": body["size_bytes"],
                    "media_type": body["media_type"],
                    "created_at": "2026-08-17T00:00:00Z",
                }
                self.artifacts.setdefault(sid, []).append(art)
                return self._json(201, art)
            if method == "GET":
                return self._json(200, {"items": self.artifacts.get(sid, [])})

        m = re.match(rf"^{BASE}/sessions/([^/]+)/events$", path)
        if m and method == "GET":
            sid = m.group(1)
            after = request.url.params.get("cursor") or request.headers.get("Last-Event-ID")
            evs = self.events.get(sid, [])
            if after:
                evs = [e for e in evs if e["cursor"] > after]
            lines = []
            for e in evs:
                lines.append(f"id: {e['cursor']}")
                lines.append("data: " + json.dumps(e))
                lines.append("")
            body = ("\n".join(lines) + "\n").encode()
            return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

        m = re.match(rf"^{BASE}/operations/([^/]+)$", path)
        if m and method == "GET":
            op = self.operations.get(m.group(1))
            if not op:
                return self._error(404, "terminal", "operation.not_found", "no such operation")
            return self._json(200, op)

        return self._error(404, "terminal", "not_found", f"unhandled {method} {path}")

    def _make_op(self, kind: str, sid: str) -> dict:
        op_id = "op-" + uuid.uuid4().hex[:12]
        op = {"id": op_id, "kind": kind, "done": True, "session_id": sid}
        self.operations[op_id] = op
        return op
