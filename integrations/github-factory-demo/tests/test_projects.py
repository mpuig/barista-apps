from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from barista_github_factory_demo import (
    Claim,
    ControllerConfig,
    DeliveryStore,
    DemoController,
    GitHubProjector,
    ProjectProjection,
    ProjectProjectionError,
    build_factory_run,
    create_app,
)


def _resolved(*, existing: bool = True) -> dict:
    items = (
        [
            {
                "id": "PVTI_item-1",
                "content": {
                    "id": "I_issue-1",
                    "url": "https://github.com/acme/demo/issues/7",
                },
            }
        ]
        if existing
        else []
    )
    return {
        "data": {
            "user": {
                "projectV2": {
                    "id": "PVT_project-1",
                    "fields": {
                        "nodes": [
                            {
                                "id": "PVTSSF_status-1",
                                "name": "Status",
                                "options": [
                                    {"id": "todo-option", "name": "Todo"},
                                    {"id": "progress-option", "name": "In Progress"},
                                    {"id": "done-option", "name": "Done"},
                                ],
                            }
                        ]
                    },
                    "items": {"nodes": items},
                }
            },
            "resource": {
                "id": "I_issue-1",
                "url": "https://github.com/acme/demo/issues/7",
            },
        }
    }


def _projector(handler) -> GitHubProjector:
    return GitHubProjector(
        token="project-only-token",
        owner="acme",
        owner_kind="user",
        project_number=2,
        status_field="Status",
        status_options={
            "accepted": "Todo",
            "running": "In Progress",
            "awaiting_input": "Todo",
            "refused": "Done",
            "succeeded": "Done",
            "failed": "Done",
        },
        client=httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="https://api.github.test/graphql",
        ),
    )


def test_existing_project_item_is_updated_from_canonical_status():
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.github.com/graphql"
        document = json.loads(request.content)
        requests.append(document)
        if "ResolveProject" in document["query"]:
            return httpx.Response(200, json=_resolved())
        return httpx.Response(
            200,
            json={
                "data": {
                    "updateProjectV2ItemFieldValue": {
                        "projectV2Item": {"id": "PVTI_item-1"}
                    }
                }
            },
        )

    result = _projector(handler).sync(
        "https://github.com/acme/demo/issues/7", "running"
    )

    assert result == ProjectProjection(item_id="PVTI_item-1", status="running")
    assert len(requests) == 2
    assert requests[1]["variables"] == {
        "project": "PVT_project-1",
        "item": "PVTI_item-1",
        "field": "PVTSSF_status-1",
        "option": "progress-option",
    }
    assert "project-only-token" not in repr(requests)


def test_missing_project_item_is_added_before_status_update():
    queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        query = json.loads(request.content)["query"]
        queries.append(query)
        if "ResolveProject" in query:
            return httpx.Response(200, json=_resolved(existing=False))
        if "AddProjectItem" in query:
            return httpx.Response(
                200,
                json={
                    "data": {"addProjectV2ItemById": {"item": {"id": "PVTI_new-item"}}}
                },
            )
        return httpx.Response(
            200,
            json={
                "data": {
                    "updateProjectV2ItemFieldValue": {
                        "projectV2Item": {"id": "PVTI_new-item"}
                    }
                }
            },
        )

    result = _projector(handler).sync(
        "https://github.com/acme/demo/issues/7", "accepted"
    )
    assert result.item_id == "PVTI_new-item"
    assert len(queries) == 3


def test_graphql_errors_are_bounded_and_do_not_expose_provider_details():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "errors": [{"message": "provider response includes project-only-token"}]
            },
        )

    with pytest.raises(ProjectProjectionError) as failure:
        _projector(handler).sync("https://github.com/acme/demo/issues/7", "accepted")
    assert str(failure.value) == "GitHub Projects operation was refused"
    assert "project-only-token" not in str(failure.value)


class RecordingProjector:
    def __init__(self, *, fail: bool = False):
        self.calls: list[tuple[str, str]] = []
        self.fail = fail
        self.closed = False

    def sync(self, issue_uri: str, status: str) -> ProjectProjection:
        self.calls.append((issue_uri, status))
        if self.fail:
            raise RuntimeError("temporary Projects outage")
        return ProjectProjection(item_id="PVTI_item", status=status)

    def close(self) -> None:
        self.closed = True


class Executor:
    def execute(self, _claim):
        return {"workflow_state": "verified_for_review"}


def _config(tmp_path: Path) -> ControllerConfig:
    return ControllerConfig(
        repository="https://github.com/acme/demo",
        webhook_secret="webhook-secret",
        github_token="forge-token",
        database=tmp_path / "controller.sqlite3",
        result_directory=tmp_path / "results",
    )


def test_projection_failure_is_durable_and_does_not_fail_workflow(tmp_path):
    selected = _config(tmp_path)
    store = DeliveryStore(selected.database)
    claim = store.claim(
        delivery_id="opened-1",
        repository=selected.repository,
        issue_number=7,
        issue_uri=selected.repository + "/issues/7",
        run_name="github-demo-issue-7-attempt-1",
    )
    projector = RecordingProjector(fail=True)
    controller = DemoController(
        selected, store=store, executor=Executor(), projector=projector
    )
    controller.submit(claim)
    controller.close()

    # Re-open because controller.close() owns and closes the supplied store.
    reopened = DeliveryStore(selected.database)
    document = reopened.get("opened-1")
    assert document is not None
    assert document["status"] == "succeeded"
    assert document["project_projection"]["desired_status"] == "succeeded"
    assert "temporary Projects outage" in document["project_projection"]["last_error"]
    assert document["project_projection"]["attempts"] >= 1
    reopened.close()


def test_startup_reconciles_project_from_durable_controller_state(tmp_path):
    selected = _config(tmp_path)
    store = DeliveryStore(selected.database)
    claim = store.claim(
        delivery_id="opened-1",
        repository=selected.repository,
        issue_number=7,
        issue_uri=selected.repository + "/issues/7",
        run_name="github-demo-issue-7-attempt-1",
    )
    store.refuse(claim.delivery_id, {"workflow_state": "refused"})
    projector = RecordingProjector()
    controller = DemoController(
        selected, store=store, executor=Executor(), projector=projector
    )
    controller.start()
    controller.close()

    assert projector.calls == [(selected.repository + "/issues/7", "refused")]
    reopened = DeliveryStore(selected.database)
    projection = reopened.get("opened-1")["project_projection"]
    assert projection["desired_status"] == projection["projected_status"] == "refused"
    assert projection["last_error"] is None
    reopened.close()


def test_manual_project_webhook_cannot_advance_canonical_workflow(tmp_path):
    selected = _config(tmp_path)
    store = DeliveryStore(selected.database)
    claim = store.claim(
        delivery_id="opened-1",
        repository=selected.repository,
        issue_number=7,
        issue_uri=selected.repository + "/issues/7",
        run_name="github-demo-issue-7-attempt-1",
    )
    store.await_input(
        claim.delivery_id,
        {
            "workflow_state": "needs_input",
            "question_digest": "sha256:" + "1" * 64,
        },
    )
    controller = DemoController(selected, store=store, executor=Executor())
    body = json.dumps(
        {"action": "edited", "projects_v2_item": {"field": "Status"}}
    ).encode()
    signature = (
        "sha256="
        + hmac.new(selected.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    )
    with TestClient(create_app(selected, controller=controller)) as client:
        response = client.post(
            "/webhooks/github",
            content=body,
            headers={
                "x-hub-signature-256": signature,
                "x-github-event": "projects_v2_item",
                "x-github-delivery": "project-edit-1",
            },
        )
        canonical = client.get("/issues/7").json()

    assert response.json() == {"accepted": False, "reason": "unsupported event"}
    assert canonical["status"] == "awaiting_input"
    controller.close()


def test_project_configuration_requires_separate_complete_authority(tmp_path):
    base = _config(tmp_path)
    with pytest.raises(ValueError, match="configured together"):
        ControllerConfig(**{**base.__dict__, "github_project_number": 2})
    with pytest.raises(ValueError, match="separate credentials"):
        ControllerConfig(
            **{
                **base.__dict__,
                "github_project_number": 2,
                "github_project_token": "forge-token",
            }
        )
    configured = ControllerConfig(
        **{
            **base.__dict__,
            "github_project_number": 2,
            "github_project_token": "project-token",
        }
    )
    public = configured.public_document()
    assert public["project"]["enabled"] is True
    assert public["project"]["number"] == 2
    assert "project-token" not in repr(public)

    repository_hash = hashlib.sha256(configured.repository.encode()).hexdigest()[:10]
    run = build_factory_run(
        configured,
        Claim(
            delivery_id="delivery",
            repository=configured.repository,
            issue_number=7,
            issue_uri=configured.repository + "/issues/7",
            status="accepted",
            run_name=f"github-{repository_hash}-issue-7-attempt-1",
        ),
    )
    assert "project-token" not in repr(run.to_document())
    assert "BARISTA_GITHUB_PROJECT" not in repr(run.to_document())
