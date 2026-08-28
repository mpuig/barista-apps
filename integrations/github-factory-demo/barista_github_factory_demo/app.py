"""Signed GitHub webhook ingress and asynchronous Factory dispatch."""

from __future__ import annotations

import concurrent.futures
import hashlib
import hmac
import json
import os
import re
import threading
from contextlib import asynccontextmanager

from barista_app_sdk.sensitive import (
    SecretLeak,
    assert_no_high_confidence_secrets,
    redact_text,
)
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .config import ControllerConfig
from .executor import FactoryRunExecutor
from .store import Claim, DeliveryStore

_DELIVERY_ID = re.compile(r"^[A-Za-z0-9-]{1,128}$")
_QUESTION_MARKER = "<!-- barista-factory-question:"


class DemoController:
    def __init__(
        self,
        config: ControllerConfig,
        *,
        store: DeliveryStore | None = None,
        executor: FactoryRunExecutor | None = None,
    ):
        self.config = config
        self.store = store or DeliveryStore(config.database)
        self.executor = executor or FactoryRunExecutor(config)
        self._pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=config.concurrency,
            thread_name_prefix="github-factory",
        )
        self._scheduled: set[str] = set()
        self._lock = threading.Lock()

    def start(self) -> None:
        for claim in self.store.recoverable():
            self.submit(claim)

    def submit(self, claim: Claim) -> None:
        with self._lock:
            if claim.run_name in self._scheduled:
                return
            self._scheduled.add(claim.run_name)
        self._pool.submit(self._process, claim)

    def _process(self, claim: Claim) -> None:
        try:
            self.store.mark_running(claim.delivery_id)
            result = self.executor.execute(claim)
            workflow_state = result.get("workflow_state")
            if workflow_state == "needs_input":
                self.store.await_input(claim.delivery_id, result)
            elif workflow_state == "refused":
                self.store.refuse(claim.delivery_id, result)
            else:
                self.store.succeed(claim.delivery_id, result)
        # This is the asynchronous process boundary: every provider, integrity,
        # forge, filesystem, and programming failure must become durable status.
        except Exception as exc:  # noqa: BLE001
            code = getattr(exc, "code", None)
            message = f"{code}: {exc}" if code else f"{type(exc).__name__}: {exc}"
            message = redact_text(
                message,
                (
                    self.config.github_token,
                    self.config.webhook_secret,
                    os.environ.get("BARISTA_HOST_API_TOKEN", ""),
                ),
            )
            try:
                assert_no_high_confidence_secrets(message)
            except SecretLeak:
                message = (
                    f"{code or type(exc).__name__}: sensitive failure details redacted"
                )
            self.store.fail(claim.delivery_id, message)
        finally:
            with self._lock:
                self._scheduled.discard(claim.run_name)

    def close(self) -> None:
        self._pool.shutdown(wait=True, cancel_futures=False)
        self.store.close()


def _signature(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def create_app(
    config: ControllerConfig | None = None,
    *,
    controller: DemoController | None = None,
) -> FastAPI:
    selected = config or ControllerConfig.from_env()
    owned = controller is None
    service = controller or DemoController(selected)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        service.start()
        try:
            yield
        finally:
            if owned:
                service.close()

    app = FastAPI(
        title="Barista GitHub Factory demo", version="0.1.0", lifespan=lifespan
    )
    app.state.controller = service

    @app.get("/healthz")
    def health() -> dict:
        return {
            "ok": True,
            "repository": selected.full_name,
            "factory_app": selected.factory_app,
            "triage_app": selected.triage_app,
            "worker_app": selected.worker_app,
        }

    @app.get("/runs/{delivery_id}")
    def run_status(delivery_id: str) -> dict:
        if not _DELIVERY_ID.fullmatch(delivery_id):
            raise HTTPException(status_code=404, detail="run not found")
        result = service.store.get(delivery_id)
        if result is None:
            raise HTTPException(status_code=404, detail="run not found")
        return result

    @app.get("/issues/{issue_number}")
    def issue_status(issue_number: int) -> dict:
        if issue_number <= 0:
            raise HTTPException(status_code=404, detail="run not found")
        result = service.store.get_issue(selected.repository, issue_number)
        if result is None:
            raise HTTPException(status_code=404, detail="run not found")
        return result

    @app.post("/webhooks/github")
    async def github_webhook(request: Request):
        length = request.headers.get("content-length")
        if length:
            try:
                if int(length) > selected.max_webhook_bytes:
                    raise HTTPException(
                        status_code=413, detail="webhook body too large"
                    )
            except ValueError as exc:
                raise HTTPException(
                    status_code=400, detail="invalid content length"
                ) from exc
        chunks = bytearray()
        async for chunk in request.stream():
            if len(chunks) + len(chunk) > selected.max_webhook_bytes:
                raise HTTPException(status_code=413, detail="webhook body too large")
            chunks.extend(chunk)
        body = bytes(chunks)
        supplied = request.headers.get("x-hub-signature-256", "")
        expected = _signature(selected.webhook_secret, body)
        if not supplied or not hmac.compare_digest(supplied, expected):
            raise HTTPException(status_code=401, detail="invalid webhook signature")

        event = request.headers.get("x-github-event", "")
        if event == "ping":
            return JSONResponse(
                status_code=202, content={"accepted": False, "reason": "ping"}
            )
        if event not in {"issues", "issue_comment"}:
            return JSONResponse(
                status_code=202,
                content={"accepted": False, "reason": "unsupported event"},
            )
        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise HTTPException(status_code=400, detail="invalid JSON") from exc
        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=400, detail="webhook payload must be an object"
            )

        repository = payload.get("repository")
        issue = payload.get("issue")
        if not isinstance(repository, dict) or not isinstance(issue, dict):
            raise HTTPException(status_code=400, detail="issue webhook is incomplete")
        if repository.get("full_name") != selected.full_name:
            raise HTTPException(
                status_code=403, detail="repository is outside demo scope"
            )
        number = issue.get("number")
        if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
            raise HTTPException(status_code=400, detail="issue number is invalid")
        issue_uri = issue.get("html_url")
        expected_issue_uri = f"{selected.repository}/issues/{number}"
        if issue_uri != expected_issue_uri or "pull_request" in issue:
            raise HTTPException(
                status_code=400, detail="issue URL does not match repository"
            )
        delivery_id = request.headers.get("x-github-delivery", "")
        if not _DELIVERY_ID.fullmatch(delivery_id):
            raise HTTPException(status_code=400, detail="delivery id is invalid")

        repository_hash = hashlib.sha256(selected.repository.encode()).hexdigest()[:10]
        run_prefix = f"github-{repository_hash}-issue-{number}"
        if event == "issues":
            if payload.get("action") != "opened":
                return JSONResponse(
                    status_code=202,
                    content={"accepted": False, "reason": "unsupported action"},
                )
            claim = service.store.claim(
                delivery_id=delivery_id,
                repository=selected.repository,
                issue_number=number,
                issue_uri=issue_uri,
                run_name=f"{run_prefix}-attempt-1",
            )
            if claim.created:
                service.submit(claim)
            return JSONResponse(
                status_code=202,
                content={
                    "accepted": True,
                    "duplicate": not claim.created,
                    "delivery_id": claim.delivery_id,
                    "run": claim.run_name,
                    "status": claim.status,
                },
            )

        if payload.get("action") != "created":
            return JSONResponse(
                status_code=202,
                content={"accepted": False, "reason": "unsupported action"},
            )
        comment = payload.get("comment")
        sender = payload.get("sender")
        if not isinstance(comment, dict) or not isinstance(sender, dict):
            raise HTTPException(status_code=400, detail="comment webhook is incomplete")
        comment_id = comment.get("id")
        login = sender.get("login")
        answer = comment.get("body")
        if (
            not isinstance(comment_id, int)
            or isinstance(comment_id, bool)
            or comment_id <= 0
            or not isinstance(login, str)
            or not isinstance(answer, str)
            or not answer.strip()
            or len(answer.encode("utf-8")) > 64 * 1024
        ):
            raise HTTPException(status_code=400, detail="comment webhook is invalid")
        try:
            assert_no_high_confidence_secrets(answer)
        except SecretLeak:
            return JSONResponse(
                status_code=202,
                content={"accepted": False, "reason": "unsafe answer"},
            )
        is_self = bool(
            selected.controller_login
            and login.casefold() == selected.controller_login.casefold()
        )
        is_bot = (
            sender.get("type") == "Bot"
            or login.casefold().endswith("[bot]")
            or _QUESTION_MARKER in answer
        )
        if is_self or is_bot or login.casefold() not in selected.responders:
            return JSONResponse(
                status_code=202,
                content={"accepted": False, "reason": "unauthorized responder"},
            )
        claim, disposition = service.store.accept_answer(
            delivery_id=delivery_id,
            repository=selected.repository,
            issue_number=number,
            comment_id=comment_id,
            answer=answer,
            run_name_prefix=run_prefix,
        )
        if claim is not None:
            service.submit(claim)
        return JSONResponse(
            status_code=202,
            content={
                "accepted": claim is not None,
                "reason": disposition,
                "run": claim.run_name if claim is not None else None,
            },
        )

    return app
