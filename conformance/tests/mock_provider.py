"""In-process mock Host API provider — a TEST DOUBLE for exercising the
conformance suite offline. It is intentionally not the real local provider
(that is apps-001 section 3); it exists only so the suite can prove it detects
pass / fail / skip and enforces the no-skip-satisfies-advertised rule.
"""

from __future__ import annotations

import base64
import functools
import importlib.util
import json
import math
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import httpx

BASE = "/v1alpha1"
REPO = Path(__file__).resolve().parents[2]
DELEGATED = "grants.delegated"

#: The reference provider's delegated grant lifetime. Long enough that the
#: ordinary tests never trip over it, short enough to be shortened in a test
#: that needs a mission to outlive one (apps-003 task 3.3).
DEFAULT_GRANT_LIFETIME_SECONDS = 900.0


def _iso(timestamp: float) -> str:
    """RFC 3339 with milliseconds.

    Whole-second precision would round a sub-second grant lifetime to zero, and a
    test that shortens the provider's lifetime instead of lengthening itself needs
    sub-second lifetimes to stay honest.
    """
    if not math.isfinite(timestamp):
        return "9999-12-31T23:59:59.000Z"
    whole = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(timestamp))
    return f"{whole}.{int((timestamp % 1) * 1000):03d}Z"


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

    resource: str = ""
    """What the grant acts on. Copied, never taken from a refresh request."""

    expires_at: float = math.inf
    revoked: bool = False

    def live(self, now: float) -> bool:
        """Expiry and revocation are the same question at the point of use: is
        this secret still one I accept? A grant that answers no cannot be
        refreshed either (design D3)."""
        return not self.revoked and now < self.expires_at

    def action_ids(self) -> list[str]:
        return sorted({action for action, _ in self.grants})

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
        grant_lifetime_seconds: float = DEFAULT_GRANT_LIFETIME_SECONDS,
        now: Optional[Callable[[], float]] = None,
        refresh_keeps_old_secret: bool = False,
        refresh_reads_request_scope: bool = False,
        refresh_supported: bool = True,
        deliver_grant_into_session: bool = True,
        refresh_unbound_grants: bool = False,
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
        self.grant_lifetime_seconds = grant_lifetime_seconds
        """How long a minted delegated grant stays live. Shortened by tests that
        need a mission to outlive one grant, instead of lengthening the test."""
        self.now = now or time.time
        """Injectable clock: a test can advance time past a grant's expiry
        without sleeping, and the provider and its caller then agree on 'now'."""
        self.refresh_keeps_old_secret = refresh_keeps_old_secret
        """Dishonest mode: refresh mints a replacement but keeps accepting the
        old secret. That turns rotation into extension — a leaked secret becomes
        permanent — and the suite must catch it."""
        self.refresh_reads_request_scope = refresh_reads_request_scope
        """Dishonest mode: refresh reads the scope from the request body. That is
        `grant.issue` wearing refresh's name (design D1), and the suite must
        catch it."""
        self.refresh_supported = refresh_supported
        """False advertises grants.delegated without offering refresh — which the
        contract now requires of anyone advertising it."""
        self.deliver_grant_into_session = deliver_grant_into_session
        """False mints grants but delivers them somewhere a client cannot read
        (a file, a metadata service). Honest, and it leaves the suite unable to
        obtain a credential — so the delegation cases skip saying so."""
        self.refresh_unbound_grants = refresh_unbound_grants
        """Dishonest mode, and the one a permissive implementation gets wrong:
        refresh a grant with no session binding. Nothing then bounds the chain,
        so a holder outlives any maximum-lifetime ceiling in steps that never
        individually exceed it."""

        self.sessions: dict[str, dict] = {}
        self.session_env: dict[str, dict[str, str]] = {}
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
        """Resolve the caller. None means 'presented a credential I do not
        accept' — unknown, expired, revoked, or already replaced by a refresh.
        All four are the same answer at the point of use."""
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return Principal(kind=TENANT)
        token = auth[len("bearer ") :].strip()
        if not self.child_authority:
            return Principal(kind=TENANT)
        principal = self.principals.get(token)
        if principal is not None and not principal.live(self.now()):
            return None
        return principal

    @staticmethod
    def _grant_env_names(manifest: dict) -> list[str]:
        """Env var names the manifest asks to have a *grant* resolved into.

        This is how an app receives its credential: a ``grant://`` secret
        reference resolved into its environment at session create. It is
        declared in the manifest, so a client can know the name without a
        private hook — and it is written exactly once, which is the whole
        reason refresh has to exist.
        """
        secrets = (manifest.get("permissions", {}) or {}).get("secrets", []) or []
        return [s["name"] for s in secrets if str(s.get("ref", "")).startswith("grant://")]

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
            resource=f"session:{session_id}",
            expires_at=self.now() + self.grant_lifetime_seconds,
        )
        # Deliver it the way a provider does: resolved into the session's
        # environment under the name the manifest declared.
        if self.deliver_grant_into_session:
            env = self.session_env.setdefault(session_id, {})
            for name in self._grant_env_names(manifest):
                env[name] = token
        return token

    def _replace_grant(self, old_token: str, principal: Principal, *, body: dict) -> dict:
        """Rotate a live grant: mint a replacement carrying the SAME resource and
        actions, and stop accepting the old secret.

        The scope is copied from the stored principal. ``body`` is only consulted
        by the dishonest ``refresh_reads_request_scope`` mode, which exists so
        the conformance suite can prove it catches a provider that turned refresh
        into issuance.
        """
        grants = set(principal.grants)
        resource = principal.resource
        may_create = principal.may_create_children
        if self.refresh_reads_request_scope:
            asked = body.get("actions")
            if asked:
                grants = {(a, "created_sessions") for a in asked}
                may_create = "session.create" in asked
            resource = body.get("resource", resource)

        new_token = "refreshed-" + uuid.uuid4().hex[:12]
        self.principals[new_token] = Principal(
            kind="app",
            session_id=principal.session_id,
            grants=grants,
            may_create_children=may_create,
            # `created` is shared, not copied: the replacement is the same holder,
            # so a session created before the rotation is still its own child.
            created=principal.created,
            wide_scope=principal.wide_scope,
            resource=resource,
            expires_at=self.now() + self.grant_lifetime_seconds,
        )
        if not self.refresh_keeps_old_secret:
            # Atomic with respect to authorization: in this store the swap is a
            # single dict mutation, so there is no instant where both work and
            # none where neither does.
            self.principals.pop(old_token, None)
        # The session's environment is deliberately NOT rewritten. A running
        # process's env cannot be changed, which is exactly why the holder has to
        # keep the response rather than re-read where it came from.
        new = self.principals[new_token]
        return {
            "secret": new_token,
            "resource": new.resource,
            "actions": new.action_ids(),
            "expires_at": _iso(new.expires_at),
        }

    def _revoke_grants_for_session(self, session_id: str) -> None:
        """A delegated grant is bound to a session; deleting the session ends the
        chain (design D5). This is the only revocation path a black-box client
        has, so the suite depends on it to prove a revoked grant is refused."""
        for principal in self.principals.values():
            if principal.session_id == session_id:
                principal.revoked = True

    def provision_delegated_probe(self, manifest: dict) -> dict:
        """TEST FIXTURE ONLY — plays the *operator*, not the suite.

        Installs a child-authority manifest, creates a coordinator session and
        one worker beneath it, and hands back the credentials this provider
        minted for each, plus a session neither of them created. The conformance
        suite never calls this: it receives the same values through
        ``ProviderConfig.delegated_probe``, exactly as an operator would supply
        them.

        Since apps-003 the suite can also acquire the same thing for itself
        through the published contract — a probe session's grant is delivered
        into its environment and refresh proves it is a live delegated grant.
        This fixture stays because operator-supplied credentials remain
        supported, and because the acquisition path has to be tested against a
        provider that did *not* hand the suite anything.
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

    def provision_unbound_grant(self, manifest: dict) -> str:
        """TEST FIXTURE ONLY — a delegated grant bound to no session.

        A provider might mint one for an operator or a service principal. It
        holds real authority, and it is exactly the grant refresh must refuse:
        with no session, nothing ends the chain, so a holder renews past any
        maximum-lifetime ceiling in steps that never exceed it. A black-box
        client cannot produce one, so the suite is handed it the way it is handed
        operator credentials.
        """
        rules = _manifest_rules()
        declared = (manifest.get("permissions", {}) or {}).get("actions")
        token = "unbound-" + uuid.uuid4().hex[:12]
        self.principals[token] = Principal(
            kind="app",
            session_id=None,
            grants={(g.action, g.scope) for g in rules.normalize(declared)},
            may_create_children=False,
            resource="account",
            expires_at=self.now() + self.grant_lifetime_seconds,
        )
        return token

    def _new_session(
        self,
        app: str,
        name: Optional[str],
        parent: Optional[str] = None,
        env: Optional[dict] = None,
    ) -> str:
        sid = "sess-" + uuid.uuid4().hex[:12]
        self.session_env[sid] = {str(k): str(v) for k, v in (env or {}).items()}
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

    def _exec_stdout(self, session_id: str, command: list) -> bytes:
        """What the command writes. ``printenv NAME`` reads the session's own
        environment, which is how a workload sees the grant a provider resolved
        into it — the only way a client outside a session can observe that the
        credential was delivered at all. Anything else echoes 'hello'."""
        if len(command) == 2 and command[0] == "printenv":
            return self.session_env.get(session_id, {}).get(str(command[1]), "").encode()
        return b"hello"

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
                401,
                "authentication",
                "authentication.credential_not_accepted",
                "the presented credential is not accepted: unknown, expired, revoked, "
                "or replaced by a refresh",
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

        m = re.match(rf"^{BASE}/apps/([^/]+)$", path)
        if m and method == "GET":
            name = m.group(1)
            manifest = self.apps.get(name)
            if manifest is None:
                return self._error(404, "terminal", "not_found", f"app '{name}' is not installed")
            return self._json(
                200,
                {
                    "name": manifest["name"],
                    "version": manifest["version"],
                    "digest": manifest["workload"]["digest"],
                    "granted_capabilities": self.capabilities,
                    "installed_at": "2026-08-17T00:00:00Z",
                    "manifest": manifest,
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
                body.get("app", "unknown"),
                body.get("name"),
                parent=principal.session_id,
                env=body.get("env"),
            )
            session = self.sessions[sid]
            if principal.kind != TENANT:
                principal.created.add(sid)
            manifest = self.apps.get(session["app"])
            if manifest is not None:
                # The provider mints the session's grant from the manifest: the
                # app's own actions when the tenant created it, the declared
                # child subset when another app's grant did. Nobody can ask for
                # either through the API — that is the point.
                self._mint(manifest, sid, child=principal.kind != TENANT)
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
                # A grant is bound to its session: deleting the session revokes
                # it, and a revoked grant can no longer be refreshed (D3/D5).
                self._revoke_grants_for_session(sid)
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
            if not principal.may("session.exec", sid):
                return self._forbid("session.exec")
            if sid not in self.sessions:
                return self._error(404, "terminal", "session.not_found", "no such session")
            command = json.loads(request.content or b"{}").get("command") or []
            stdout = self._exec_stdout(sid, command)
            op_id = "op-" + uuid.uuid4().hex[:12]
            # event_cursor is an EXCLUSIVE resume point captured BEFORE the exec
            # events, so reading after it yields this command's stdout onward.
            event_cursor = f"{self._seq:012d}"
            self._emit(sid, "exec.stdout", {"chunk": base64.b64encode(stdout).decode()}, op_id)
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

        if path == f"{BASE}/grants/refresh" and method == "POST":
            if DELEGATED not in self.capabilities or not self.refresh_supported:
                return self._error(
                    501, "capability", "capability.unsupported",
                    "grants.delegated is not supported by this provider",
                )
            if principal.kind == TENANT:
                # It authenticates; it just is not a grant. There is nothing to
                # rotate, and minting one from a tenant credential would be
                # issuance under another name.
                return self._error(
                    403, "authorization", "grant.absent",
                    "the presented credential is not a delegated grant, so there is "
                    "nothing to refresh",
                )
            if principal.session_id is None and not self.refresh_unbound_grants:
                # No session, no bound on the chain. Refusing is what keeps a
                # maximum-lifetime ceiling meaning "come back and re-decide".
                # The refusal leaves the presented secret working: the failure
                # direction is rollback, never revoke-then-fail.
                return self._error(
                    403, "authorization", "grant.unbound",
                    "this grant is bound to no session, so a refresh chain on it would "
                    "have nothing to end it; obtain a new grant instead",
                )
            # Liveness was already decided in _principal: an expired, revoked or
            # already-rotated secret never gets this far (401).
            token = request.headers.get("authorization", "")[len("bearer ") :].strip()
            body = {}
            if request.content:
                try:
                    parsed = json.loads(request.content)
                    body = parsed if isinstance(parsed, dict) else {}
                except json.JSONDecodeError:
                    body = {}
            return self._json(200, self._replace_grant(token, principal, body=body))

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
