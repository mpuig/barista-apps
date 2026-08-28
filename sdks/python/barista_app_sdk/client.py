"""The Barista Host API client.

Provider-neutral: construct it with a Config (endpoint + credential source) and
the same app code runs against a local provider or Barista Cloud. Mutations are
idempotent and safely retryable; operations can be awaited; events resume from a
cursor. A transport may be injected for in-process testing.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Iterator, Optional

import httpx

from . import errors
from .attach import AttachFrame
from .config import Config
from .models import Artifact, Discovery, Event, ExecHandle, Grant, InstalledApp, Operation, Session
from .runs import APP_RUN_ENV, AppRun, RunOperation, validate_run

BASE = "/v1alpha1"
MANIFEST_MEDIA_TYPE = "application/vnd.barista.app-manifest.v1alpha1+json"


class BaristaClient:
    def __init__(
        self,
        config: Config,
        *,
        transport: Optional[httpx.BaseTransport] = None,
        max_retries: int = 3,
        retry_backoff: float = 0.1,
    ):
        self.config = config
        headers = {"accept": "application/json"}
        token = config.resolved_token()
        if token:
            headers["authorization"] = f"Bearer {token}"
        self._http = httpx.Client(
            base_url=config.endpoint, headers=headers,
            transport=transport, timeout=config.timeout_seconds,
        )
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff
        self._discovery: Optional[Discovery] = None
        # Bumped whenever the credential is replaced. A request that was in
        # flight across a rotation sees a 401 for a credential that was valid
        # when it was sent; comparing generations tells that apart from a
        # credential that is genuinely dead.
        self._credential_generation = 0

    @classmethod
    def from_env(cls, **kw: Any) -> "BaristaClient":
        return cls(Config.from_env(), **kw)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "BaristaClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- low level -------------------------------------------------------- #
    @staticmethod
    def new_idempotency_key() -> str:
        return "idem-" + uuid.uuid4().hex

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        content: Optional[bytes] = None,
        headers: Optional[dict] = None,
        params: Optional[dict] = None,
        idempotency_key: Optional[str] = None,
        expected: tuple[int, ...] = (200, 201, 202),
    ) -> httpx.Response:
        hdrs = dict(headers or {})
        if idempotency_key:
            hdrs["Idempotency-Key"] = idempotency_key

        attempt = 0
        while True:
            attempt += 1
            generation = self._credential_generation
            try:
                resp = self._http.request(
                    method, path, json=json_body, content=content, headers=hdrs, params=params
                )
            except httpx.TransportError as exc:
                # A lost response is safe to retry only because the key is stable.
                if idempotency_key and attempt <= self._max_retries:
                    time.sleep(self._retry_backoff * attempt)
                    continue
                raise errors.UnavailableError(f"transport error: {exc}", code="transport") from exc

            if resp.status_code in expected:
                return resp

            if (
                resp.status_code == 401
                and self._credential_generation != generation
                and attempt <= self._max_retries
            ):
                # The credential rotated while this request was in flight. There
                # is no overlap window by design — the old secret stops working
                # the instant the new one is issued — so a request that raced the
                # rotation is retried with the new credential. A 401 the server
                # answered means it did nothing, so this is safe without a key.
                continue

            err = errors.from_response(resp.status_code, _safe_json(resp))
            # Retry only the transient class, and only with a stable key.
            if err.retryable and idempotency_key and attempt <= self._max_retries:
                time.sleep(self._retry_backoff * attempt)
                continue
            raise err

    # -- discovery / negotiation ----------------------------------------- #
    def discovery(self, *, refresh: bool = False) -> Discovery:
        if self._discovery is None or refresh:
            resp = self._request("GET", f"{BASE}/discovery", expected=(200,))
            self._discovery = Discovery.parse(resp.json())
        return self._discovery

    def supports(self, capability: str) -> bool:
        return self.discovery().supports(capability)

    def negotiate(self, *, required: Optional[list[str]] = None) -> Discovery:
        """Verify required capabilities are advertised before creating anything."""
        disc = self.discovery(refresh=True)
        missing = [c for c in (required or []) if c not in disc.capabilities]
        if missing:
            raise errors.CapabilityError(
                f"provider is missing required capabilities: {', '.join(missing)}",
                code="capability.missing",
                details={"missing": missing},
            )
        return disc

    # -- apps ------------------------------------------------------------- #
    def install_app(self, manifest: dict, *, idempotency_key: Optional[str] = None) -> dict:
        resp = self._request(
            "POST", f"{BASE}/apps", content=json.dumps(manifest).encode(),
            headers={"content-type": MANIFEST_MEDIA_TYPE},
            idempotency_key=idempotency_key or self.new_idempotency_key(),
            expected=(201,),
        )
        return resp.json()

    def get_installed_app(self, name: str) -> InstalledApp:
        resp = self._request("GET", f"{BASE}/apps/{name}", expected=(200,))
        return InstalledApp.parse(resp.json())

    def launch_app_run(
        self,
        run: AppRun,
        manifest: dict,
        *,
        install: bool = True,
        env: Optional[dict[str, str]] = None,
        idempotency_key: Optional[str] = None,
    ) -> tuple[Session, RunOperation]:
        """Validate and launch one typed App Run in an owning session.

        Validation is deliberately first: an undeclared binding, delivery, or
        invalid embedded input cannot leave an installed app or session behind.
        The run's content id supplies stable keys when the caller does not, so a
        process-level retry converges on the same installation and session.
        """
        operation = validate_run(run, manifest)
        launch_env = dict(env or {})
        if APP_RUN_ENV in launch_env:
            raise errors.InvalidRequestError(
                f"{APP_RUN_ENV} is reserved for the canonical App Run envelope",
                code="app_run.reserved_env",
                error_class="invalid_request",
            )
        launch_env[APP_RUN_ENV] = run.canonical_bytes().decode("utf-8")

        required = [
            item["capability"]
            for item in manifest.get("capabilities", {}).get("required", [])
        ]
        self.negotiate(required=required)

        key = idempotency_key or "app-run-" + run.content_id().split(":", 1)[1]
        if install:
            self.install_app(manifest, idempotency_key=key + "-install")
        session = self.ensure_session(
            manifest["name"],
            name=run.name,
            env=launch_env,
            metadata={
                "sh.barista.app-run": {
                    "content_id": run.content_id(),
                    "operation": run.operation,
                    "lifecycle": operation.lifecycle,
                }
            },
            idempotency_key=key + "-session",
        )
        return session, operation

    # -- sessions --------------------------------------------------------- #
    def ensure_session(
        self,
        app: str,
        *,
        name: Optional[str] = None,
        args: Optional[list[str]] = None,
        env: Optional[dict[str, str]] = None,
        metadata: Optional[dict] = None,
        idempotency_key: Optional[str] = None,
    ) -> Session:
        body: dict[str, Any] = {"app": app}
        if name is not None:
            body["name"] = name
        if args:
            body["args"] = args
        if env:
            body["env"] = env
        if metadata:
            body["metadata"] = metadata
        # A stable key is generated once so a lost response never duplicates.
        resp = self._request(
            "POST", f"{BASE}/sessions", json_body=body,
            idempotency_key=idempotency_key or self.new_idempotency_key(),
            expected=(200, 201),
        )
        return Session.parse(resp.json())

    def get_session(self, session_id: str) -> Session:
        resp = self._request("GET", f"{BASE}/sessions/{session_id}", expected=(200,))
        return Session.parse(resp.json())

    def list_sessions(self, *, app: Optional[str] = None) -> list[Session]:
        params = {"app": app} if app else None
        resp = self._request("GET", f"{BASE}/sessions", params=params, expected=(200,))
        return [Session.parse(s) for s in resp.json().get("items", [])]

    def delete_session(self, session_id: str, *, idempotency_key: Optional[str] = None) -> Operation:
        resp = self._request(
            "DELETE", f"{BASE}/sessions/{session_id}",
            idempotency_key=idempotency_key or self.new_idempotency_key(), expected=(202,),
        )
        return Operation.parse(resp.json())

    def pause(self, session_id: str, *, idempotency_key: Optional[str] = None) -> Operation:
        resp = self._request(
            "POST", f"{BASE}/sessions/{session_id}/pause",
            idempotency_key=idempotency_key or self.new_idempotency_key(), expected=(202,),
        )
        return Operation.parse(resp.json())

    def resume(self, session_id: str, *, idempotency_key: Optional[str] = None) -> Operation:
        resp = self._request(
            "POST", f"{BASE}/sessions/{session_id}/resume",
            idempotency_key=idempotency_key or self.new_idempotency_key(), expected=(202,),
        )
        return Operation.parse(resp.json())

    # -- exec / operations ------------------------------------------------ #
    def exec(
        self,
        session_id: str,
        command: list[str],
        *,
        env: Optional[dict[str, str]] = None,
        working_dir: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        idempotency_key: Optional[str] = None,
    ) -> ExecHandle:
        body: dict[str, Any] = {"command": command}
        if env:
            body["env"] = env
        if working_dir:
            body["working_dir"] = working_dir
        if timeout_seconds:
            body["timeout_seconds"] = timeout_seconds
        resp = self._request(
            "POST", f"{BASE}/sessions/{session_id}/exec", json_body=body,
            idempotency_key=idempotency_key or self.new_idempotency_key(), expected=(200,),
        )
        d = resp.json()
        return ExecHandle(operation_id=d["operation_id"], event_cursor=d["event_cursor"])

    def get_operation(self, operation_id: str) -> Operation:
        resp = self._request("GET", f"{BASE}/operations/{operation_id}", expected=(200,))
        return Operation.parse(resp.json())

    def wait_operation(
        self,
        operation_id: str,
        *,
        timeout: float = 30.0,
        poll: float = 0.05,
        max_poll: float = 2.0,
    ) -> Operation:
        deadline = time.time() + timeout
        interval = poll
        while True:
            op = self.get_operation(operation_id)
            if op.done:
                if op.error:
                    raise errors.from_response(op.error.get("status", 0) or 500, op.error)
                return op
            if time.time() > deadline:
                raise errors.UnavailableError(
                    f"operation {operation_id} did not complete in {timeout}s", code="operation.timeout"
                )
            time.sleep(min(interval, max(0.0, deadline - time.time())))
            # Exponential backoff capped at max_poll: a fast op still resolves in
            # one or two polls, but a long-running one stops hammering the gateway
            # (a 50ms fixed poll is ~20 req/s per in-flight operation).
            interval = min(interval * 2, max_poll)

    # -- delegated grants ------------------------------------------------- #
    def set_credential(self, secret: str) -> None:
        """Present ``secret`` as the bearer credential from now on.

        Rotation has no overlap window: the previous secret stops working the
        instant the replacement is issued. Requests already in flight are handled
        by ``_request``, which retries a 401 whose credential changed underneath
        it rather than reporting authority it still has as authority lost.
        """
        self._http.headers["authorization"] = f"Bearer {secret}"
        self._credential_generation += 1

    def refresh_grant(self) -> Grant:
        """Refresh the delegated grant this client authenticates with, and start
        presenting the replacement.

        Requires ``grants.delegated``. The credential is the subject: there is
        nothing to pass, and nothing that could widen the result.

        **Not retried, deliberately.** The operation takes no idempotency key
        because a replayable rotation would mean the provider keeping a second
        live copy of a credential, so a blind retry after a lost response would
        rotate again from a secret that no longer works. Losing this response is
        losing the authority — refresh with enough margin that there is time to
        report it and be re-provisioned.
        """
        resp = self._request("POST", f"{BASE}/grants/refresh", expected=(200,))
        grant = Grant.parse(resp.json())
        self.set_credential(grant.secret)
        return grant

    # -- artifacts -------------------------------------------------------- #
    def register_artifact(
        self, session_id: str, *, name: str, digest: str, size_bytes: int, media_type: str,
        metadata: Optional[dict] = None, idempotency_key: Optional[str] = None,
    ) -> Artifact:
        body = {"name": name, "digest": digest, "size_bytes": size_bytes, "media_type": media_type}
        if metadata:
            body["metadata"] = metadata
        resp = self._request(
            "POST", f"{BASE}/sessions/{session_id}/artifacts", json_body=body,
            idempotency_key=idempotency_key or self.new_idempotency_key(), expected=(201,),
        )
        return Artifact.parse(resp.json())

    def list_artifacts(self, session_id: str) -> list[Artifact]:
        resp = self._request("GET", f"{BASE}/sessions/{session_id}/artifacts", expected=(200,))
        return [Artifact.parse(a) for a in resp.json().get("items", [])]

    # -- events ----------------------------------------------------------- #
    def events(self, session_id: str, *, cursor: Optional[str] = None, max_events: int = 100) -> Iterator[Event]:
        headers = {"accept": "text/event-stream"}
        if cursor:
            headers["Last-Event-ID"] = cursor
        params = {"cursor": cursor} if cursor else None
        count = 0
        with self._http.stream(
            "GET", f"{BASE}/sessions/{session_id}/events", params=params, headers=headers
        ) as resp:
            if resp.status_code != 200:
                resp.read()
                raise errors.from_response(resp.status_code, _safe_json(resp))
            data_lines: list[str] = []
            for line in resp.iter_lines():
                if line == "":
                    if data_lines:
                        yield Event.parse(json.loads("\n".join(data_lines)))
                        data_lines = []
                        count += 1
                        if count >= max_events:
                            return
                    continue
                if line.startswith(":"):
                    continue
                if line.startswith("data:"):
                    data_lines.append(line[len("data:"):].lstrip())

    # -- attach ----------------------------------------------------------- #
    def open_attach(self, session_id: str, *, mode: str = "raw"):
        """Open a WebSocket attach stream. Requires the ``ws`` extra and a
        provider that exposes attach. Returns an object yielding AttachFrames."""
        if mode not in ("raw", "pty"):
            raise ValueError("mode must be 'raw' or 'pty'")
        try:
            from websockets.sync.client import connect  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("attach requires the 'ws' extra (pip install barista-app-sdk[ws])") from exc
        ws_base = self.config.endpoint.replace("http://", "ws://").replace("https://", "wss://")
        url = f"{ws_base}{BASE}/sessions/{session_id}/attach?mode={mode}"
        headers = {}
        token = self.config.resolved_token()
        if token:
            headers["authorization"] = f"Bearer {token}"
        return _AttachStream(connect(url, additional_headers=headers))


class _AttachStream:  # pragma: no cover - requires a live WS provider
    def __init__(self, ws):
        self._ws = ws

    def send(self, frame: AttachFrame) -> None:
        self._ws.send(frame.to_wire())

    def __iter__(self) -> Iterator[AttachFrame]:
        for message in self._ws:
            yield AttachFrame.from_wire(message)

    def close(self) -> None:
        self._ws.close()


def _safe_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except Exception:  # noqa: BLE001
        return None
