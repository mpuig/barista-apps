"""In-process mock Host API provider — a TEST DOUBLE for exercising the
conformance suite offline. It is intentionally not the real local provider
(that is apps-001 section 3); it exists only so the suite can prove it detects
pass / fail / skip and enforces the no-skip-satisfies-advertised rule.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Optional

import httpx

BASE = "/v1alpha1"


class MockProvider:
    def __init__(
        self,
        *,
        name: str = "mock",
        version: str = "0.0.0",
        capabilities: Optional[list[str]] = None,
        fake_unadvertised_pause: bool = False,
    ):
        self.name = name
        self.version = version
        self.capabilities = capabilities or []
        self.fake_unadvertised_pause = fake_unadvertised_pause

        self.sessions: dict[str, dict] = {}
        self.idem_sessions: dict[str, str] = {}
        self.operations: dict[str, dict] = {}
        self.artifacts: dict[str, list[dict]] = {}
        self.events: dict[str, list[dict]] = {}
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

    # -- router ----------------------------------------------------------- #
    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        idem = request.headers.get("Idempotency-Key")

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
            sid = "sess-" + uuid.uuid4().hex[:12]
            session = {
                "id": sid,
                "name": body.get("name"),
                "app": body.get("app", "unknown"),
                "state": "running",
                "created_at": "2026-08-17T00:00:00Z",
            }
            self.sessions[sid] = session
            self.artifacts[sid] = []
            if idem:
                self.idem_sessions[idem] = sid
            self._emit(sid, "session.state_changed", {"state": "running"})
            return self._json(201, session)

        if path == f"{BASE}/sessions" and method == "GET":
            return self._json(200, {"items": list(self.sessions.values())})

        m = re.match(rf"^{BASE}/sessions/([^/]+)$", path)
        if m:
            sid = m.group(1)
            if method == "GET":
                if sid not in self.sessions:
                    return self._error(404, "terminal", "session.not_found", "no such session")
                return self._json(200, self.sessions[sid])
            if method == "DELETE":
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
            cursor = self._emit(sid, "exec.stdout", {"chunk": "aGVsbG8="}, op_id)
            self._emit(sid, "exec.exit", {"exit_code": 0}, op_id)
            self.operations[op_id] = {
                "id": op_id, "kind": "exec", "done": True, "session_id": sid,
                "result": {"exit_code": 0}, "last_event_cursor": cursor,
            }
            return self._json(200, {"operation_id": op_id, "event_cursor": cursor})

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
