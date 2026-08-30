from __future__ import annotations

import copy
import json
import sys

import pytest

from barista_github_factory_demo.activity_projection import (
    DeploymentRunner,
    program_activity,
)
from barista_github_factory_demo.app import DemoController
from barista_github_factory_demo.config import ControllerConfig
from barista_github_factory_demo.store import Claim, DeliveryStore


def _config(tmp_path, **changes) -> ControllerConfig:
    values = {
        "repository": "https://github.com/acme/widgets",
        "webhook_secret": "webhook-secret",
        "github_token": "forge-token",
        "database": tmp_path / "state.sqlite3",
        "activity_endpoint": "https://cloud.example",
        "activity_token": "activity-token",
        "activity_source_url": "https://factory.example",
    }
    values.update(changes)
    return ControllerConfig(**values)


def _program() -> dict:
    return {
        "program_id": "program-21",
        "repository": "https://github.com/acme/widgets",
        "issue_number": 21,
        "issue_uri": "https://github.com/acme/widgets/issues/21",
        "status": "accepted",
        "brd": {
            "pr_uri": "https://github.com/acme/widgets/pull/22",
            "head_commit": "1" * 40,
            "digest": "sha256:" + "a" * 64,
            "approved_commit": "2" * 40,
            "approved_by": "reviewer",
            "approved_at": 1_700_000_100,
        },
        "plan_digest": "sha256:" + "b" * 64,
        "features": [
            {
                "id": "status-api",
                "title": "Status API",
                "dependencies": [],
                "issue_uri": "https://github.com/acme/widgets/issues/23",
                "pr_uri": "https://github.com/acme/widgets/pull/26",
                "head_commit": "3" * 40,
                "merged_commit": "4" * 40,
            },
            {
                "id": "dashboard",
                "title": "Dashboard",
                "dependencies": ["status-api"],
                "issue_uri": "https://github.com/acme/widgets/issues/24",
                "pr_uri": None,
                "head_commit": None,
                "merged_commit": None,
            },
        ],
        "acceptance": {
            "schema_version": "v1alpha1",
            "program": "program-21",
            "assembled_commit": "4" * 40,
            "features": ["status-api", "dashboard"],
            "command_digest": "sha256:" + "c" * 64,
            "exit_code": 0,
            "accepted": True,
        },
        "error": None,
        "created_at": 1_700_000_000,
        "updated_at": 1_700_000_200,
    }


def test_accepted_program_maps_to_generic_bounded_activity(tmp_path):
    document = program_activity(
        _program(),
        {
            "question_digest": "sha256:" + "d" * 64,
            "answer_count": 1,
            "updated_at": 1_700_000_050,
        },
        _config(tmp_path),
    )

    assert document["source"] == {
        "id": "software-factory",
        "label": "Software Factory",
        "url": "https://factory.example",
    }
    assert document["kind"] == "product-program"
    assert document["phase"] == "succeeded"
    assert document["title"] == "widgets · Program #21"
    event_ids = [event["id"] for event in document["events"]]
    assert event_ids.index("brd-published") < event_ids.index("brd-approved")
    assert event_ids.index("feature-status-api-published") < event_ids.index(
        "feature-status-api-verified"
    ) < event_ids.index("feature-status-api-merged")
    assert {event["id"] for event in document["events"]} >= {
        "program-created",
        "clarification-requested",
        "clarification-received",
        "brd-published",
        "brd-approved",
        "plan-validated",
        "feature-status-api-published",
        "feature-status-api-verified",
        "feature-status-api-merged",
        "program-accepted",
    }
    assert all(link["url"].startswith("https://") for link in document["links"])
    assert {artifact["id"] for artifact in document["artifacts"]} == {
        "brd",
        "plan",
        "accepted-source",
        "acceptance-result",
        "acceptance-command",
    }
    assert document["actions"] == [
        {
            "id": "deploy-1",
            "label": "Deploy",
            "description": "A trusted deployment runner has not been configured for this source.",
            "available": False,
            "confirmation": "Request deployment of the exact accepted commit?",
        }
    ]
    assert "github_token" not in json.dumps(document)


def test_deployment_time_does_not_retimestamp_historical_program_events(tmp_path):
    deployed = {
        "request_id": "ar-succeeded",
        "program_id": "program-21",
        "state": "succeeded",
        "result": {
            "deployment_id": "deployment-program-21",
            "session_name": "product-program-21",
            "endpoint": "https://app.example",
            "image_digest": "sha256:" + "f" * 64,
        },
        "error": None,
        "attempts": 1,
        "created_at": 1_700_000_210,
        "updated_at": 1_700_000_220,
    }
    document = program_activity(
        _program(), None, _config(tmp_path), deployed, deployment_count=1
    )
    assert document["events"][-1]["id"] == "product-deployed"
    assert document["events"][-2]["id"] == "program-accepted"


def test_failed_deployment_exposes_a_fresh_human_retry_identity(tmp_path):
    config = _config(
        tmp_path, activity_deploy_command=("/usr/local/bin/deploy-product",)
    )
    failed = {
        "request_id": "ar-failed",
        "program_id": "program-21",
        "state": "failed",
        "result": None,
        "error": "endpoint verification failed",
        "attempts": 1,
        "created_at": 1_700_000_210,
        "updated_at": 1_700_000_220,
    }
    document = program_activity(
        _program(), None, config, failed, deployment_count=1
    )
    assert document["actions"][0]["id"] == "deploy-2"
    assert document["actions"][0]["available"] is True
    assert not any(event["id"] == "product-deployed" for event in document["events"])


def test_projection_revision_only_advances_for_changed_source_content(tmp_path):
    store = DeliveryStore(tmp_path / "projection.sqlite3")
    document = program_activity(_program(), None, _config(tmp_path))

    first = store.desire_activity("program-21", document)
    replay = store.desire_activity("program-21", copy.deepcopy(document))
    assert first["revision"] == replay["revision"] == 1
    target = store.activity_target("program-21")
    assert target is not None

    store.activity_succeeded("program-21", target["content_digest"])
    assert store.activity_target("program-21") is None

    changed = copy.deepcopy(document)
    changed["status_label"] = "Deployed"
    second = store.desire_activity("program-21", changed)
    assert second["revision"] == 2
    target = store.activity_target("program-21")
    assert target is not None
    store.activity_failed("program-21", target["content_digest"], "upstream unavailable\nretry")
    assert store.activity_target("program-21") is not None
    store.close()


def test_program_event_journal_is_ordered_idempotent_and_immutable(tmp_path):
    store = DeliveryStore(tmp_path / "events.sqlite3")
    claim = type(
        "ClaimValue",
        (),
        {
            "repository": "https://github.com/acme/widgets",
            "issue_number": 21,
            "issue_uri": "https://github.com/acme/widgets/issues/21",
            "delivery_id": "delivery-21",
        },
    )()
    store.ensure_program("program-21", claim)
    store.record_program_event(
        "program-21",
        "decision-1",
        "decision.received",
        "Decision received",
        occurred_at=1_700_000_010,
    )
    # A delivery replay may observe a later wall clock but cannot duplicate history.
    store.record_program_event(
        "program-21",
        "decision-1",
        "decision.received",
        "Decision received",
        occurred_at=1_700_000_020,
    )
    events = store.program_events("program-21")
    assert [event["id"] for event in events].count("decision-1") == 1
    with pytest.raises(ValueError, match="reused"):
        store.record_program_event(
            "program-21", "decision-1", "decision.received", "Changed title"
        )
    store.close()


@pytest.mark.parametrize("other", ["forge", "project", "host"])
def test_activity_authority_must_be_separate(tmp_path, other):
    changes = {"activity_token": "same-token"}
    if other == "forge":
        changes["github_token"] = "same-token"
    elif other == "project":
        changes.update(
            github_project_token="same-token", github_project_number=4
        )
    else:
        changes["host_api_token"] = "same-token"
    with pytest.raises(ValueError, match="separate credentials"):
        _config(tmp_path, **changes)


def test_activity_configuration_is_paired_and_credential_free(tmp_path):
    with pytest.raises(ValueError, match="configured together"):
        _config(tmp_path, activity_token=None)
    with pytest.raises(ValueError, match="credential-free HTTPS"):
        _config(tmp_path, activity_endpoint="https://user:secret@cloud.example")
    with pytest.raises(ValueError, match="absolute executable"):
        _config(tmp_path, activity_deploy_command=("deploy-product",))


def test_fixed_deployment_adapter_receives_exact_identity_and_returns_verified_result(
    tmp_path,
):
    adapter = tmp_path / "adapter.py"
    captured = tmp_path / "captured.json"
    adapter.write_text(
        """import json, sys
request = json.load(sys.stdin)
open(sys.argv[1], 'w').write(json.dumps(request, sort_keys=True))
print(json.dumps({
  'schema_version': 'v1alpha1',
  'operation_id': request['operation_id'],
  'deployment_id': 'deployment-program-21',
  'endpoint': 'https://program-21.apps.example',
  'image_digest': 'sha256:' + 'e' * 64,
  'session_name': 'product-program-21'
}))
"""
    )
    runner = DeploymentRunner(
        (sys.executable, str(adapter), str(captured)), timeout_seconds=30
    )
    result = runner.deploy("ar-123", _program())

    request = json.loads(captured.read_text())
    assert request["operation_id"] == "ar-123"
    assert request["accepted_commit"] == "4" * 40
    assert request["repository"] == "https://github.com/acme/widgets"
    assert result["endpoint"] == "https://program-21.apps.example"
    assert result["image_digest"] == "sha256:" + "e" * 64


class _ActionStore:
    def __init__(self):
        self.program = _program()
        self.deployment = None

    def get_program(self, program_id):
        assert program_id == "program-21"
        return self.program

    def claim_deployment(self, request_id, program_id):
        assert request_id == "ar-123"
        assert program_id == "program-21"
        return True

    def complete_deployment(self, request_id, result):
        self.deployment = (request_id, result)

    def fail_deployment(self, request_id, message):
        raise AssertionError(message)


class _ActionPublisher:
    def __init__(self):
        self.resolutions = []

    def resolve_action(self, request_id, source_id, state, **values):
        self.resolutions.append((request_id, source_id, state, values))


class _ActionRunner:
    def __init__(self):
        self.calls = 0

    def deploy(self, request_id, program):
        self.calls += 1
        assert request_id == "ar-123"
        assert program["acceptance"]["accepted"] is True
        return {
            "message": "verified",
            "links": [
                {
                    "rel": "endpoint",
                    "label": "Open application",
                    "url": "https://app.example",
                }
            ],
            "artifacts": [],
            "deployment_id": "deployment-program-21",
            "session_name": "product-program-21",
            "endpoint": "https://app.example",
            "image_digest": "sha256:" + "f" * 64,
        }


def test_human_action_is_source_executed_and_durably_resolved():
    controller = object.__new__(DemoController)
    controller.store = _ActionStore()
    controller.activity_publisher = _ActionPublisher()
    controller.deployment_runner = _ActionRunner()
    controller._queue_activity_program = lambda program_id: None

    controller._handle_activity_action(
        {
            "request_id": "ar-123",
            "stream_id": "program-21",
            "source_id": "software-factory",
            "action_id": "deploy-1",
        }
    )

    assert controller.store.program["status"] == "accepted"
    assert controller.store.deployment[0] == "ar-123"
    assert [item[2] for item in controller.activity_publisher.resolutions] == [
        "running",
        "succeeded",
    ]


def test_activity_request_cannot_deploy_unaccepted_program():
    controller = object.__new__(DemoController)
    store = _ActionStore()
    store.program["status"] = "implementing"
    publisher = _ActionPublisher()
    runner = _ActionRunner()
    controller.store = store
    controller.activity_publisher = publisher
    controller.deployment_runner = runner
    controller._queue_activity_program = lambda program_id: None

    controller._handle_activity_action(
        {
            "request_id": "ar-123",
            "stream_id": "program-21",
            "source_id": "software-factory",
            "action_id": "deploy-1",
        }
    )

    assert runner.calls == 0
    assert publisher.resolutions[0][2] == "failed"
    assert store.program["status"] == "implementing"


class _FailingPublisher:
    def publish(self, program_id, document):
        raise RuntimeError("projection unavailable")


def test_projection_failure_cannot_fail_accepted_program(tmp_path):
    config = _config(tmp_path)
    store = DeliveryStore(tmp_path / "failure.sqlite3")
    claim = Claim(
        delivery_id="delivery-21",
        repository=config.repository,
        issue_number=21,
        issue_uri=f"{config.repository}/issues/21",
        status="accepted",
        run_name="program-21-attempt-1",
        workflow_kind="program_brd",
        program_id="program-21",
    )
    store.ensure_program("program-21", claim)
    store._update_program(  # noqa: SLF001 - exercise the projection boundary only
        "program-21",
        status="accepted",
        acceptance_json=json.dumps(_program()["acceptance"]),
    )
    desired = store.desire_activity(
        "program-21", program_activity(_program(), None, config)
    )
    target = store.activity_target("program-21")
    assert target is not None

    controller = object.__new__(DemoController)
    controller.config = config
    controller.store = store
    controller.activity_publisher = _FailingPublisher()
    controller._publish_activity(
        "program-21", desired, target["content_digest"]
    )

    assert store.get_program("program-21")["status"] == "accepted"
    assert store.activity_target("program-21") is not None
    store.close()
