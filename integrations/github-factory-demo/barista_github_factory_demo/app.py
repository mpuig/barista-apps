"""Signed GitHub webhook ingress and asynchronous Factory dispatch."""

from __future__ import annotations

import calendar
import concurrent.futures
import hashlib
import hmac
import json
import os
import re
import threading
import time
from contextlib import asynccontextmanager

from barista_app_sdk.sensitive import (
    SecretLeak,
    assert_no_high_confidence_secrets,
    redact_text,
)
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .activity_projection import ActivityPublisher, DeploymentRunner, program_activity
from .config import ControllerConfig
from .executor import FactoryRunExecutor
from .program import GitHubProgramForge, ProgramRunExecutor
from .projects import GitHubProjector, Projector
from .store import Claim, DeliveryStore

_DELIVERY_ID = re.compile(r"^[A-Za-z0-9-]{1,128}$")
_QUESTION_MARKER = "<!-- barista-factory-question:"
_PROGRAM_MARKER = "[barista:product-program]"
_FEATURE_MARKER = "<!-- barista-program-feature:v1 "


class DemoController:
    def __init__(
        self,
        config: ControllerConfig,
        *,
        store: DeliveryStore | None = None,
        executor: FactoryRunExecutor | None = None,
        projector: Projector | None = None,
        program_executor: ProgramRunExecutor | None = None,
        program_forge: GitHubProgramForge | None = None,
        activity_publisher: ActivityPublisher | None = None,
        deployment_runner: DeploymentRunner | None = None,
    ):
        self.config = config
        self.store = store or DeliveryStore(config.database)
        self.executor = executor or FactoryRunExecutor(config)
        self.program_executor = program_executor or ProgramRunExecutor(config)
        self.program_forge = program_forge or GitHubProgramForge(
            token=config.github_token, repository=config.repository
        )
        if projector is not None:
            self.projector = projector
        elif config.project_enabled:
            assert config.github_project_token is not None
            assert config.github_project_number is not None
            self.projector = GitHubProjector(
                token=config.github_project_token,
                owner=config.project_owner,
                owner_kind=config.github_project_owner_kind,
                project_number=config.github_project_number,
                status_field=config.github_project_status_field,
                status_options=config.project_status_options,
            )
        else:
            self.projector = None
        if activity_publisher is not None:
            self.activity_publisher = activity_publisher
        elif config.activity_enabled:
            assert config.activity_endpoint is not None
            assert config.activity_token is not None
            self.activity_publisher = ActivityPublisher(
                config.activity_endpoint, config.activity_token
            )
        else:
            self.activity_publisher = None
        if deployment_runner is not None:
            self.deployment_runner = deployment_runner
        elif config.activity_deploy_enabled:
            self.deployment_runner = DeploymentRunner(
                config.activity_deploy_command,
                config.activity_deploy_timeout_seconds,
            )
        else:
            self.deployment_runner = None
        self._activity_stop = threading.Event()
        self._activity_thread: threading.Thread | None = None
        self._pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=config.concurrency,
            thread_name_prefix="github-factory",
        )
        self._program_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="github-program",
        )
        self._projection_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="github-project",
        )
        self._scheduled: set[str] = set()
        self._lock = threading.Lock()

    def start(self) -> None:
        if self.projector is not None:
            for issue_uri, status, details in self.store.projection_targets():
                self._queue_projection(issue_uri, status, details)
        if self.activity_publisher is not None:
            for program in self.store.list_programs():
                self._queue_activity_program(str(program["program_id"]))
            if self.deployment_runner is not None:
                self._activity_thread = threading.Thread(
                    target=self._activity_action_loop,
                    name="github-factory-activity-actions",
                    daemon=True,
                )
                self._activity_thread.start()
        for claim in self.store.recoverable():
            self.submit(claim)
        for program_id, status in self.store.recoverable_programs():
            self._program_pool.submit(self._recover_program, program_id, status)

    def submit(self, claim: Claim) -> None:
        self._queue_projection(claim.issue_uri, claim.status)
        with self._lock:
            if claim.run_name in self._scheduled:
                return
            self._scheduled.add(claim.run_name)
        self._pool.submit(self._process, claim)

    def _process(self, claim: Claim) -> None:
        try:
            self.store.mark_running(claim.delivery_id)
            self._queue_projection(claim.issue_uri, "running")
            result = self.executor.execute(claim)
            workflow_state = result.get("workflow_state")
            if workflow_state == "needs_input":
                if claim.workflow_kind == "program_brd" and claim.program_id:
                    self.store.record_brd_waiting(claim.program_id)
                self.store.await_input(claim.delivery_id, result)
                self._queue_projection(claim.issue_uri, "awaiting_input")
            elif workflow_state == "refused":
                if claim.program_id:
                    self.store.fail_program(
                        claim.program_id, "product work was refused"
                    )
                self.store.refuse(claim.delivery_id, result)
                self._queue_projection(claim.issue_uri, "refused")
            else:
                # Persist the idempotent program transition before terminalizing
                # its delivery. A crash can then replay the same result instead
                # of losing the approval/merge correlation target.
                if claim.workflow_kind == "program_brd" and claim.program_id:
                    self.store.record_brd_pr(claim.program_id, result)
                elif (
                    claim.workflow_kind == "feature"
                    and claim.program_id
                    and claim.feature_id
                ):
                    self.store.record_feature_pr(
                        claim.program_id, claim.feature_id, result
                    )
                self.store.succeed(claim.delivery_id, result)
                self._queue_projection(claim.issue_uri, "succeeded")
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
            if claim.program_id:
                if claim.feature_id:
                    self.store.fail_feature(claim.program_id, claim.feature_id, message)
                else:
                    self.store.fail_program(claim.program_id, message)
            self.store.fail(claim.delivery_id, message)
            self._queue_projection(claim.issue_uri, "failed")
        finally:
            if claim.program_id:
                self._project_program(claim.program_id)
            with self._lock:
                self._scheduled.discard(claim.run_name)

    def submit_brd_merge(
        self,
        *,
        program_id: str,
        commit: str,
        actor: str,
        merged_at: int,
        delivery_id: str,
    ) -> None:
        self._program_pool.submit(
            self._approve_and_plan,
            program_id,
            commit,
            actor,
            merged_at,
            delivery_id,
        )

    def submit_feature_merge(
        self,
        *,
        program_id: str,
        feature_id: str,
        commit: str,
        delivery_id: str,
    ) -> None:
        self._program_pool.submit(
            self._merge_and_release,
            program_id,
            feature_id,
            commit,
            delivery_id,
        )

    def _approve_and_plan(
        self,
        program_id: str,
        commit: str,
        actor: str,
        merged_at: int,
        delivery_id: str,
    ) -> None:
        try:
            program = self.store.get_program(program_id)
            if program is None:
                self.store.dispose_external_delivery(delivery_id, "stale_brd_merge")
                return
            if program["status"] != "awaiting_brd_merge":
                disposition = (
                    "duplicate_brd_merge"
                    if program["brd"]["approved_commit"] == commit
                    else "stale_brd_merge"
                )
                self.store.dispose_external_delivery(delivery_id, disposition)
                return
            raw = self.program_forge.read_file(str(program["brd"]["path"]), commit)
            digest = "sha256:" + hashlib.sha256(raw).hexdigest()
            self.store.approve_brd(
                program_id,
                commit=commit,
                digest=digest,
                actor=actor,
                approved_at=merged_at,
            )
            self._plan_publish_release(program_id)
            self.store.dispose_external_delivery(delivery_id, "accepted_brd_merge")
        except Exception as exc:  # noqa: BLE001 - persistent orchestration boundary
            self.store.fail_program(program_id, self._safe_failure(exc))
            self.store.dispose_external_delivery(delivery_id, "failed_brd_merge")
        finally:
            self._project_program(program_id)

    def _plan_publish_release(self, program_id: str) -> None:
        program = self.store.get_program(program_id)
        if program is None:
            raise KeyError(program_id)
        if program["plan_digest"] is None:
            plan, digest = self.program_executor.plan(program)
            self.store.save_plan(program_id, plan, digest)
        self._publish_features(program_id)
        self.store.mark_implementing(program_id)
        self._release_ready(program_id)

    def _publish_features(self, program_id: str) -> None:
        program = self.store.get_program(program_id)
        if program is None or not isinstance(program.get("plan_digest"), str):
            raise ValueError("program has no verified plan")
        for feature in self.store.unpublished_features(program_id):
            issue = self.program_forge.ensure_feature_issue(
                program_id=program_id,
                feature=feature,
                plan_digest=program["plan_digest"],
            )
            number = issue.get("number")
            uri = issue.get("html_url")
            expected = f"{self.config.repository}/issues/{number}"
            if (
                not isinstance(number, int)
                or isinstance(number, bool)
                or number <= 0
                or uri != expected
            ):
                raise ValueError("feature issue response changed delivery identity")
            self.store.assign_feature_issue(
                program_id, feature["id"], number=number, uri=uri
            )
            self._queue_projection(uri, "accepted")

    def _release_ready(self, program_id: str) -> None:
        repository_hash = hashlib.sha256(self.config.repository.encode()).hexdigest()[
            :10
        ]
        for feature in self.store.ready_features(program_id):
            run_name = (
                f"github-{repository_hash}-issue-{feature['issue_number']}-attempt-1"
            )
            claim = self.store.claim_feature(program_id, feature["id"], run_name)
            self.submit(claim)
        if self.store.all_features_merged(program_id):
            self._accept_program(program_id)

    def _merge_and_release(
        self,
        program_id: str,
        feature_id: str,
        commit: str,
        delivery_id: str,
    ) -> None:
        try:
            program = self.store.get_program(program_id)
            feature = next(
                (
                    item
                    for item in (program or {}).get("features", [])
                    if item["id"] == feature_id
                ),
                None,
            )
            if feature is None or feature["status"] != "awaiting_merge":
                disposition = (
                    "duplicate_feature_merge"
                    if feature is not None and feature["merged_commit"] == commit
                    else "stale_feature_merge"
                )
                self.store.dispose_external_delivery(delivery_id, disposition)
                return
            self.store.merge_feature(program_id, feature_id, commit)
            self._release_ready(program_id)
            self.store.dispose_external_delivery(delivery_id, "accepted_feature_merge")
        except Exception as exc:  # noqa: BLE001
            self.store.fail_program(program_id, self._safe_failure(exc))
            self.store.dispose_external_delivery(delivery_id, "failed_feature_merge")
        finally:
            self._project_program(program_id)

    def _accept_program(self, program_id: str) -> None:
        self.store.mark_accepting(program_id)
        program = self.store.get_program(program_id)
        if program is None:
            raise KeyError(program_id)
        result = self.program_executor.accept(program)
        self.store.complete_program(program_id, result)

    def _recover_program(self, program_id: str, status: str) -> None:
        try:
            if status in {"planning", "publishing_features"}:
                self._plan_publish_release(program_id)
            elif status == "implementing":
                self._publish_features(program_id)
                self._release_ready(program_id)
            elif status == "accepting":
                self._accept_program(program_id)
        except Exception as exc:  # noqa: BLE001
            self.store.fail_program(program_id, self._safe_failure(exc))
        finally:
            self._project_program(program_id)

    def _project_program(self, program_id: str) -> None:
        self._queue_activity_program(program_id)
        if self.projector is None:
            return
        program = self.store.get_program(program_id)
        if program is None:
            return
        program_status = {
            "brd_running": "running",
            "brd_needs_input": "accepted",
            "awaiting_brd_merge": "accepted",
            "planning": "running",
            "publishing_features": "running",
            "implementing": "running",
            "accepting": "running",
            "accepted": "succeeded",
            "failed": "failed",
        }.get(program["status"], "accepted")
        self._queue_projection(
            program["issue_uri"],
            program_status,
            {
                "work_type": "Program",
                "program": program_id,
                "result": program["status"],
                "pr": program["brd"]["pr_uri"] or "",
            },
        )
        for feature in program["features"]:
            if not feature["issue_uri"]:
                continue
            status = {
                "planned": "accepted",
                "blocked": "accepted",
                "running": "running",
                "awaiting_merge": "running",
                "merged": "succeeded",
                "failed": "failed",
            }.get(feature["status"], "accepted")
            self._queue_projection(
                feature["issue_uri"],
                status,
                {
                    "work_type": "Feature",
                    "program": program_id,
                    "feature": feature["id"],
                    "attempt": 1,
                    "dependency": ", ".join(feature["dependencies"]) or "none",
                    "result": feature["status"],
                    "pr": feature["pr_uri"] or "",
                },
            )

    def _queue_activity_program(self, program_id: str) -> None:
        if self.activity_publisher is None:
            return
        program = self.store.get_program(program_id)
        if program is None:
            return
        delivery = self.store.get_issue(
            str(program["repository"]), int(program["issue_number"])
        )
        deployment = self.store.latest_deployment(program_id)
        journal = self.store.program_events(program_id)
        self.store.desire_activity(
            program_id,
            program_activity(program, delivery, self.config, deployment, journal),
        )
        target = self.store.activity_target(program_id)
        if target is not None:
            self._projection_pool.submit(
                self._publish_activity,
                program_id,
                target["document"],
                target["content_digest"],
            )

    def _publish_activity(
        self, program_id: str, document: dict, digest: str
    ) -> None:
        assert self.activity_publisher is not None
        try:
            self.activity_publisher.publish(program_id, document)
            self.store.activity_succeeded(program_id, digest)
        except Exception as exc:  # noqa: BLE001 - optional projection boundary
            message = redact_text(
                f"{type(exc).__name__}: {exc}",
                (
                    self.config.activity_token or "",
                    self.config.github_token,
                    self.config.github_project_token or "",
                    self.config.host_api_token or "",
                    self.config.webhook_secret,
                ),
            )
            try:
                assert_no_high_confidence_secrets(message)
            except SecretLeak:
                message = "ActivityProjectionError: sensitive failure details redacted"
            self.store.activity_failed(program_id, digest, message)

    def _activity_action_loop(self) -> None:
        assert self.activity_publisher is not None
        while not self._activity_stop.is_set():
            try:
                requests = [
                    *self.activity_publisher.action_requests("requested"),
                    *self.activity_publisher.action_requests("running"),
                ]
                seen: set[str] = set()
                for request in requests:
                    request_id = request.get("request_id")
                    if not isinstance(request_id, str) or request_id in seen:
                        continue
                    seen.add(request_id)
                    self._handle_activity_action(request)
            except Exception:  # noqa: BLE001 - polling is optional and retryable
                pass
            self._activity_stop.wait(5.0)

    def _handle_activity_action(self, request: dict) -> None:
        assert self.activity_publisher is not None
        assert self.deployment_runner is not None
        request_id = request.get("request_id")
        program_id = request.get("stream_id")
        source_id = request.get("source_id")
        if not all(
            isinstance(value, str) for value in (request_id, program_id, source_id)
        ):
            return
        assert isinstance(request_id, str)
        assert isinstance(program_id, str)
        assert isinstance(source_id, str)
        if source_id != "software-factory" or request.get("action_id") != "deploy":
            self.activity_publisher.resolve_action(
                request_id,
                source_id,
                "failed",
                message="The source does not implement this action.",
            )
            return
        program = self.store.get_program(program_id)
        if program is None or program.get("status") != "accepted":
            self.activity_publisher.resolve_action(
                request_id,
                source_id,
                "failed",
                message="The exact accepted program is not available for deployment.",
            )
            return
        if not self.store.claim_deployment(request_id, program_id):
            self._resolve_stored_deployment(request_id, source_id)
            return
        self.activity_publisher.resolve_action(
            request_id,
            source_id,
            "running",
            message="The source-side deployment runner is verifying the accepted artifact.",
        )
        try:
            result = self.deployment_runner.deploy(request_id, program)
            self.store.complete_deployment(request_id, result)
            self._queue_activity_program(program_id)
            self.activity_publisher.resolve_action(
                request_id,
                source_id,
                "succeeded",
                message=result["message"],
                links=result["links"],
                artifacts=result["artifacts"],
            )
        except Exception as exc:  # noqa: BLE001 - durable action boundary
            message = self._safe_failure(exc)
            self.store.fail_deployment(request_id, message)
            self.activity_publisher.resolve_action(
                request_id, source_id, "failed", message=message
            )

    def _resolve_stored_deployment(self, request_id: str, source_id: str) -> None:
        assert self.activity_publisher is not None
        deployment = self.store.get_deployment(request_id)
        if deployment is None:
            return
        result = deployment.get("result") or {}
        if deployment["state"] == "succeeded":
            self.activity_publisher.resolve_action(
                request_id,
                source_id,
                "succeeded",
                message=result.get("message"),
                links=result.get("links", []),
                artifacts=result.get("artifacts", []),
            )
        elif deployment["state"] == "failed":
            self.activity_publisher.resolve_action(
                request_id,
                source_id,
                "failed",
                message=deployment.get("error"),
            )

    def _safe_failure(self, exc: Exception) -> str:
        message = redact_text(
            f"{type(exc).__name__}: {exc}",
            (
                self.config.github_token,
                self.config.github_project_token or "",
                self.config.activity_token or "",
                self.config.webhook_secret,
                os.environ.get("BARISTA_HOST_API_TOKEN", ""),
            ),
        )
        try:
            assert_no_high_confidence_secrets(message)
        except SecretLeak:
            return f"{type(exc).__name__}: sensitive failure details redacted"
        return message

    def _queue_projection(
        self, issue_uri: str, status: str, details: dict | None = None
    ) -> None:
        if self.projector is None:
            return
        self.store.desire_projection(issue_uri, status, details)
        self._projection_pool.submit(self._project, issue_uri, status, details)

    def _project(
        self, issue_uri: str, status: str, details: dict | None = None
    ) -> None:
        assert self.projector is not None
        try:
            result = self.projector.sync(issue_uri, status, details)
            self.store.projection_succeeded(issue_uri, status, result.item_id)
        except Exception as exc:  # noqa: BLE001 - optional integration boundary
            message = redact_text(
                f"{type(exc).__name__}: {exc}",
                (
                    self.config.github_project_token or "",
                    self.config.github_token,
                    self.config.webhook_secret,
                ),
            )
            try:
                assert_no_high_confidence_secrets(message)
            except SecretLeak:
                message = "ProjectProjectionError: sensitive failure details redacted"
            self.store.projection_failed(issue_uri, status, message)

    def close(self) -> None:
        self._activity_stop.set()
        if self._activity_thread is not None:
            self._activity_thread.join(timeout=10.0)
        # Program transitions may release Factory claims, so drain that
        # producer before shutting down the Factory execution pool.
        self._program_pool.shutdown(wait=True, cancel_futures=False)
        self._pool.shutdown(wait=True, cancel_futures=False)
        self._projection_pool.shutdown(wait=True, cancel_futures=False)
        if self.projector is not None:
            self.projector.close()
        if self.activity_publisher is not None:
            self.activity_publisher.close()
        self.program_forge.close()
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
            "brd_author_app": selected.brd_author_app,
            "planner_app": selected.planner_app,
            "feature_worker_app": selected.feature_worker_app,
            "activity": selected.public_document()["activity"],
            "project": selected.public_document()["project"],
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

    @app.get("/programs/{program_id}")
    def program_status(program_id: str) -> dict:
        if re.fullmatch(r"program-[1-9][0-9]{0,9}", program_id) is None:
            raise HTTPException(status_code=404, detail="program not found")
        result = service.store.get_program(program_id)
        if result is None:
            raise HTTPException(status_code=404, detail="program not found")
        return {
            **result,
            "events": service.store.program_events(program_id),
            "deployment": service.store.latest_deployment(program_id),
        }

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
        if event not in {"issues", "issue_comment", "pull_request"}:
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
        if not isinstance(repository, dict):
            raise HTTPException(status_code=400, detail="webhook repository is missing")
        if repository.get("full_name") != selected.full_name:
            raise HTTPException(
                status_code=403, detail="repository is outside demo scope"
            )
        delivery_id = request.headers.get("x-github-delivery", "")
        if not _DELIVERY_ID.fullmatch(delivery_id):
            raise HTTPException(status_code=400, detail="delivery id is invalid")

        if event == "pull_request":
            if payload.get("action") != "closed":
                return JSONResponse(
                    status_code=202,
                    content={"accepted": False, "reason": "unsupported action"},
                )
            pull = payload.get("pull_request")
            if not isinstance(pull, dict):
                raise HTTPException(status_code=400, detail="pull request is missing")
            number = pull.get("number")
            uri = pull.get("html_url")
            if (
                not isinstance(number, int)
                or isinstance(number, bool)
                or number <= 0
                or uri != f"{selected.repository}/pull/{number}"
                or pull.get("merged") is not True
            ):
                return JSONResponse(
                    status_code=202,
                    content={
                        "accepted": False,
                        "reason": "unmerged or invalid pull request",
                    },
                )
            target = service.store.pull_target(selected.repository, number)
            if target is None:
                return JSONResponse(
                    status_code=202,
                    content={"accepted": False, "reason": "unrelated pull request"},
                )
            head = pull.get("head") or {}
            base = pull.get("base") or {}
            merged_by = pull.get("merged_by") or {}
            actor = merged_by.get("login")
            merge_commit = pull.get("merge_commit_sha")
            merged_at_raw = pull.get("merged_at")
            if (
                uri != target["expected_uri"]
                or head.get("sha") != target["expected_head"]
                or (head.get("repo") or {}).get("full_name") != selected.full_name
                or base.get("ref") != selected.base_ref
                or (base.get("repo") or {}).get("full_name") != selected.full_name
                or not isinstance(actor, str)
                or actor.casefold() not in selected.responders
                or not isinstance(merge_commit, str)
                or re.fullmatch(r"[0-9a-f]{40}", merge_commit) is None
                or not isinstance(merged_at_raw, str)
            ):
                return JSONResponse(
                    status_code=202,
                    content={
                        "accepted": False,
                        "reason": "unauthorized or stale merge",
                    },
                )
            try:
                merged_at = calendar.timegm(
                    time.strptime(merged_at_raw, "%Y-%m-%dT%H:%M:%SZ")
                )
            except ValueError:
                raise HTTPException(status_code=400, detail="merge time is invalid")
            if not service.store.claim_external_delivery(delivery_id, event):
                return JSONResponse(
                    status_code=202,
                    content={"accepted": False, "reason": "duplicate"},
                )
            if target["kind"] == "brd":
                service.submit_brd_merge(
                    program_id=target["program_id"],
                    commit=merge_commit,
                    actor=actor,
                    merged_at=merged_at,
                    delivery_id=delivery_id,
                )
            else:
                service.submit_feature_merge(
                    program_id=target["program_id"],
                    feature_id=target["feature_id"],
                    commit=merge_commit,
                    delivery_id=delivery_id,
                )
            return JSONResponse(
                status_code=202,
                content={
                    "accepted": True,
                    "kind": target["kind"],
                    "program": target["program_id"],
                },
            )

        issue = payload.get("issue")
        if not isinstance(issue, dict):
            raise HTTPException(status_code=400, detail="issue webhook is incomplete")
        number = issue.get("number")
        if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
            raise HTTPException(status_code=400, detail="issue number is invalid")
        issue_uri = issue.get("html_url")
        expected_issue_uri = f"{selected.repository}/issues/{number}"
        if issue_uri != expected_issue_uri or "pull_request" in issue:
            raise HTTPException(
                status_code=400, detail="issue URL does not match repository"
            )

        repository_hash = hashlib.sha256(selected.repository.encode()).hexdigest()[:10]
        run_prefix = f"github-{repository_hash}-issue-{number}"
        if event == "issues":
            if payload.get("action") != "opened":
                return JSONResponse(
                    status_code=202,
                    content={"accepted": False, "reason": "unsupported action"},
                )
            issue_body = issue.get("body")
            if isinstance(issue_body, str) and _FEATURE_MARKER in issue_body:
                return JSONResponse(
                    status_code=202,
                    content={
                        "accepted": False,
                        "reason": "program feature is dependency-gated by controller",
                    },
                )
            is_program = (
                isinstance(issue_body, str) and _PROGRAM_MARKER in issue_body.casefold()
            )
            program_id = f"program-{number}" if is_program else None
            claim = service.store.claim(
                delivery_id=delivery_id,
                repository=selected.repository,
                issue_number=number,
                issue_uri=issue_uri,
                run_name=f"{run_prefix}-attempt-1",
                workflow_kind="program_brd" if is_program else "issue",
                program_id=program_id,
            )
            if is_program and program_id is not None:
                service.store.ensure_program(program_id, claim)
                service._project_program(program_id)
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
            if claim.workflow_kind == "program_brd" and claim.program_id:
                service.store.record_brd_running(claim.program_id)
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
