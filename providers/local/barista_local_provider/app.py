"""The Host API core-profile router, as a Starlette ASGI app.

Maps the open Host API onto a NodeClient (Contract A) plus a local durable
store. Framework-light and transport-agnostic: the same app serves over a Unix
socket in production and is driven in-process by httpx.ASGITransport in tests.
"""

from __future__ import annotations

import base64
import functools
import json
import os
from pathlib import Path
from typing import Optional

from jsonschema import Draft202012Validator
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import errors
from .capabilities import host_api_capabilities
from .node import InstanceRequest, NodeClient, NodeNotFound, NodeUnsupported
from .store import Store

PROVIDER_NAME = "barista-local"
PROVIDER_VERSION = "0.1.0a1"
CONTRACT_VERSION = "v1alpha1"


def _contracts_dir() -> Path:
    override = os.environ.get("BARISTA_CONTRACTS_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "contracts"


@functools.lru_cache(maxsize=1)
def _manifest_validator() -> Draft202012Validator:
    schema = json.loads(
        (_contracts_dir() / "app-manifest" / "v1alpha1" / "schema.json").read_text()
    )
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


class LocalProvider:
    def __init__(self, node: NodeClient, store: Store, *, token: Optional[str] = None):
        self.node = node
        self.store = store
        self.token = token
        self._caps = host_api_capabilities(node.node_info())

    # -- auth ------------------------------------------------------------- #
    def authorized(self, request: Request) -> bool:
        if not self.token:
            return True  # loopback/unix-socket single-user default
        return request.headers.get("authorization") == f"Bearer {self.token}"

    def _idem(self, request: Request) -> Optional[str]:
        return request.headers.get("Idempotency-Key")

    def _replay_operation(self, idem: Optional[str], kind: str) -> Optional[dict]:
        if not idem:
            return None
        op_id = self.store.idempotent_lookup(idem, kind)
        return self.store.get_operation(op_id) if op_id else None

    # -- routes ----------------------------------------------------------- #
    async def discovery(self, request: Request) -> Response:
        return JSONResponse(
            {
                "contract_versions": [CONTRACT_VERSION],
                "provider": {"name": PROVIDER_NAME, "version": PROVIDER_VERSION},
                "core_profile": True,
                "capabilities": self._caps,
                "limits": {"max_concurrent_sessions": 0},
                "extensions": {},
            }
        )

    async def install_app(self, request: Request) -> Response:
        try:
            manifest = json.loads(await request.body())
        except json.JSONDecodeError:
            return errors.invalid_request("body is not valid JSON")
        problems = sorted(_manifest_validator().iter_errors(manifest), key=lambda e: list(e.path))
        if problems:
            first = problems[0]
            loc = "/".join(str(p) for p in first.path) or "<root>"
            return errors.invalid_request(f"manifest invalid at {loc}: {first.message}", "manifest.invalid")
        # Reject before any side effect when a required capability is unmet
        # (Host API installApp contract).
        required = [
            c["capability"] for c in manifest.get("capabilities", {}).get("required", [])
        ]
        missing = [c for c in required if c not in self._caps]
        if missing:
            return errors.capability_unsupported(
                f"app requires capabilities this provider does not advertise: {', '.join(missing)}"
            )
        granted = [c for c in self._advertised_for(manifest)]
        rec = self.store.install_app(manifest, granted)
        return JSONResponse(
            {
                "name": rec["name"],
                "version": rec["version"],
                "digest": rec["digest"],
                "granted_capabilities": granted,
                "installed_at": rec["installed_at"],
            },
            status_code=201,
        )

    async def get_installed_app(self, request: Request) -> Response:
        name = request.path_params["appName"]
        rec = self.store.get_app(name)
        if rec is None:
            return errors.not_found(f"app '{name}' is not installed")
        # The store contains the validated manifest and reference-only secret
        # declarations. Provider-resolved values are never stored in it and
        # therefore cannot leak through this read surface.
        return JSONResponse(
            {
                "name": rec["name"],
                "version": rec["version"],
                "digest": rec["digest"],
                "granted_capabilities": json.loads(rec["granted_capabilities"]),
                "installed_at": rec["installed_at"],
                "manifest": json.loads(rec["manifest"]),
            }
        )

    def _advertised_for(self, manifest: dict) -> list[str]:
        required = [c["capability"] for c in manifest.get("capabilities", {}).get("required", [])]
        optional = [c["capability"] for c in manifest.get("capabilities", {}).get("optional", [])]
        wanted = set(required + optional)
        return [c for c in self._caps if c in wanted]

    async def ensure_session(self, request: Request) -> Response:
        body = json.loads(await request.body())
        app_name = body.get("app")
        if not app_name:
            return errors.invalid_request("field 'app' is required")

        idem = self._idem(request)
        existing_id = self.store.idempotent_lookup(idem, "ensure")
        if existing_id:
            session = self.store.get_session(existing_id)
            if session:
                return JSONResponse(session, status_code=200)

        app = self.store.get_app(app_name)
        if not app:
            return errors.invalid_request(f"app '{app_name}' is not installed", "app.not_installed")
        manifest = json.loads(app["manifest"])
        workload = manifest["workload"]

        import uuid

        node_instance_id = "inst-" + uuid.uuid4().hex
        start_cmd = list(workload["entrypoint"]) + list(body.get("args", []))
        try:
            self.node.create_and_start(
                InstanceRequest(
                    instance_id=node_instance_id,
                    image=workload["image"],
                    digest=workload["digest"],
                    arch=workload["architectures"][0],
                    start_cmd=start_cmd,
                    env=body.get("env", {}),
                    workdir=workload.get("working_dir"),
                )
            )
        except NodeUnsupported as exc:
            return errors.capability_unsupported(str(exc))

        session = self.store.create_session(
            node_instance_id=node_instance_id,
            app=app_name,
            name=body.get("name"),
            metadata=body.get("metadata"),
        )
        self.store.append_event(session["id"], "session.state_changed", {"state": "running"})
        self.store.idempotent_record(idem, "ensure", session["id"])
        return JSONResponse(session, status_code=201)

    async def get_session(self, request: Request) -> Response:
        sid = request.path_params["sessionId"]
        session = self.store.get_session(sid)
        if not session:
            return errors.not_found("no such session")
        # Refresh state from the node without leaking any node internals. Only
        # write (a serialized SQLite commit) when the state actually changed, so
        # a status poll stays a read.
        inst_id = self.store.node_instance_id(sid)
        inst = self.node.get(inst_id) if inst_id else None
        if inst and inst.state != session["state"]:
            self.store.set_session_state(sid, inst.state)
            session["state"] = inst.state
        return JSONResponse(session)

    async def list_sessions(self, request: Request) -> Response:
        app = request.query_params.get("app")
        return JSONResponse({"items": self.store.list_sessions(app)})

    async def delete_session(self, request: Request) -> Response:
        sid = request.path_params["sessionId"]
        idem = self._idem(request)
        replay = self._replay_operation(idem, "delete")
        if replay:
            return JSONResponse(replay, status_code=202)
        session = self.store.get_session(sid)
        if not session:
            return errors.not_found("no such session")
        inst_id = self.store.node_instance_id(sid)
        if inst_id:
            try:
                self.node.destroy(inst_id)
            except NodeNotFound:
                pass
        op = self.store.create_operation("delete", sid, done=True)
        self.store.delete_session(sid)
        self.store.idempotent_record(idem, "delete", op["id"])
        return JSONResponse(op, status_code=202)

    async def _lifecycle(self, request: Request, verb: str) -> Response:
        sid = request.path_params["sessionId"]
        idem = self._idem(request)
        replay = self._replay_operation(idem, verb)
        if replay:
            return JSONResponse(replay, status_code=202)
        if "session.pause_resume" not in self._caps:
            return errors.capability_unsupported("session.pause_resume is not supported by this provider")
        inst_id = self.store.node_instance_id(sid)
        if not inst_id:
            return errors.not_found("no such session")
        try:
            getattr(self.node, verb)(inst_id)
        except NodeUnsupported as exc:
            return errors.capability_unsupported(str(exc))
        new_state = "paused" if verb == "pause" else "running"
        self.store.set_session_state(sid, new_state)
        op = self.store.create_operation(verb, sid, done=True)
        self.store.append_event(sid, "session.state_changed", {"state": new_state}, op["id"])
        self.store.idempotent_record(idem, verb, op["id"])
        return JSONResponse(op, status_code=202)

    async def pause(self, request: Request) -> Response:
        return await self._lifecycle(request, "pause")

    async def resume(self, request: Request) -> Response:
        return await self._lifecycle(request, "resume")

    async def exec_session(self, request: Request) -> Response:
        sid = request.path_params["sessionId"]
        inst_id = self.store.node_instance_id(sid)
        if not inst_id:
            return errors.not_found("no such session")
        idem = self._idem(request)
        replay = self._replay_operation(idem, "exec")
        if replay:
            return JSONResponse(
                {"operation_id": replay["id"], "event_cursor": replay.get("last_event_cursor", "")}
            )
        body = json.loads(await request.body())
        command = body.get("command")
        if not command:
            return errors.invalid_request("field 'command' is required")
        # Capture the resume point BEFORE the exec events: cursors are exclusive
        # (read_events returns seq > cursor), so handing back the newest existing
        # cursor makes the caller's next read start at this command's stdout.
        event_cursor = self.store.current_max_cursor(sid)
        try:
            result = self.node.exec(
                inst_id, command, env=body.get("env"), workdir=body.get("working_dir"),
                timeout_seconds=body.get("timeout_seconds"),
            )
        except NodeNotFound:
            return errors.not_found("no such session")
        self.store.append_event(
            sid, "exec.stdout", {"chunk": base64.b64encode(result.stdout).decode()}
        )
        if result.stderr:
            self.store.append_event(
                sid, "exec.stderr", {"chunk": base64.b64encode(result.stderr).decode()}
            )
        op = self.store.create_operation(
            "exec", sid, done=True, result={"exit_code": result.exit_code}, last_cursor=event_cursor
        )
        self.store.append_event(sid, "exec.exit", {"exit_code": result.exit_code}, op["id"])
        self.store.idempotent_record(idem, "exec", op["id"])
        return JSONResponse({"operation_id": op["id"], "event_cursor": event_cursor})

    async def register_artifact(self, request: Request) -> Response:
        sid = request.path_params["sessionId"]
        if not self.store.get_session(sid):
            return errors.not_found("no such session")
        idem = self._idem(request)
        if idem:
            existing = self.store.idempotent_lookup(idem, "artifact")
            if existing:
                art = self.store.get_artifact(existing)
                if art:
                    return JSONResponse(art, status_code=201)
        body = json.loads(await request.body())
        for field in ("name", "digest", "size_bytes", "media_type"):
            if field not in body:
                return errors.invalid_request(f"field '{field}' is required")
        art = self.store.register_artifact(sid, body)
        self.store.append_event(sid, "artifact.registered", {"artifact_id": art["id"]})
        self.store.idempotent_record(idem, "artifact", art["id"])
        return JSONResponse(art, status_code=201)

    async def list_artifacts(self, request: Request) -> Response:
        sid = request.path_params["sessionId"]
        return JSONResponse({"items": self.store.list_artifacts(sid)})

    async def get_operation(self, request: Request) -> Response:
        op = self.store.get_operation(request.path_params["operationId"])
        if not op:
            return errors.not_found("no such operation")
        return JSONResponse(op)

    async def attach(self, request: Request) -> Response:
        # Attach is part of the core profile; this backend has no PTY/byte stream
        # to upgrade to, so it returns the contract's structured 426 rather than
        # Starlette's plain 404. A backend that can attach upgrades to a WebSocket.
        sid = request.path_params["sessionId"]
        if not self.store.get_session(sid):
            return errors.not_found("no such session")
        return errors.error(
            426, "capability", "attach.upgrade_required",
            "this provider's node backend does not support interactive attach",
        )

    async def events(self, request: Request) -> Response:
        sid = request.path_params["sessionId"]
        cursor = request.query_params.get("cursor") or request.headers.get("Last-Event-ID")
        if cursor is not None and not cursor.isdigit():
            return errors.invalid_request(
                f"cursor must be a numeric resume point, got {cursor!r}", "cursor.invalid"
            )
        evs = self.store.read_events(sid, after_cursor=cursor, limit=100)
        lines = []
        for ev in evs:
            lines.append(f"id: {ev['cursor']}")
            lines.append("data: " + json.dumps(ev))
            lines.append("")
        body = ("\n".join(lines) + "\n").encode()
        return Response(body, media_type="text/event-stream")


def build_app(node: NodeClient, store: Store, *, token: Optional[str] = None) -> Starlette:
    p = LocalProvider(node, store, token=token)
    base = "/v1alpha1"
    routes = [
        Route(f"{base}/discovery", p.discovery, methods=["GET"]),
        Route(f"{base}/apps", p.install_app, methods=["POST"]),
        Route(f"{base}/apps/{{appName}}", p.get_installed_app, methods=["GET"]),
        Route(f"{base}/sessions", p.ensure_session, methods=["POST"]),
        Route(f"{base}/sessions", p.list_sessions, methods=["GET"]),
        Route(f"{base}/sessions/{{sessionId}}", p.get_session, methods=["GET"]),
        Route(f"{base}/sessions/{{sessionId}}", p.delete_session, methods=["DELETE"]),
        Route(f"{base}/sessions/{{sessionId}}/pause", p.pause, methods=["POST"]),
        Route(f"{base}/sessions/{{sessionId}}/resume", p.resume, methods=["POST"]),
        Route(f"{base}/sessions/{{sessionId}}/exec", p.exec_session, methods=["POST"]),
        Route(f"{base}/sessions/{{sessionId}}/attach", p.attach, methods=["GET"]),
        Route(f"{base}/sessions/{{sessionId}}/artifacts", p.register_artifact, methods=["POST"]),
        Route(f"{base}/sessions/{{sessionId}}/artifacts", p.list_artifacts, methods=["GET"]),
        Route(f"{base}/sessions/{{sessionId}}/events", p.events, methods=["GET"]),
        Route(f"{base}/operations/{{operationId}}", p.get_operation, methods=["GET"]),
    ]
    middleware = [Middleware(_AuthMiddleware, provider=p)]
    app = Starlette(routes=routes, middleware=middleware)
    app.state.provider = p
    return app


class _AuthMiddleware(BaseHTTPMiddleware):
    """Enforce the bearer token on every Host API route (except discovery, the
    pre-auth negotiation) when a token is configured. Centralized so no handler
    can forget it. With no token (the loopback/Unix-socket default) it is a
    no-op."""

    def __init__(self, app, provider: "LocalProvider"):
        super().__init__(app)
        self.provider = provider

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if (
            self.provider.token
            and path.startswith("/v1alpha1")
            and path != "/v1alpha1/discovery"
            and not self.provider.authorized(request)
        ):
            return errors.error(401, "authentication", "unauthorized", "missing or bad token")
        return await call_next(request)
