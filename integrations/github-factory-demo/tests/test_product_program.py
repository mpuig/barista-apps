from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path

from barista_github_factory_demo import (
    ControllerConfig,
    DeliveryStore,
    DemoController,
    create_app,
)
from fastapi.testclient import TestClient


class FactoryExecutor:
    def __init__(self):
        self.calls = []
        self.prs = {
            "program_brd": (40, "a" * 40),
            "status-api": (41, "b" * 40),
            "event-store": (42, "c" * 40),
            "dashboard": (43, "d" * 40),
        }

    def execute(self, claim):
        self.calls.append(claim)
        if claim.workflow_kind == "program_brd" and claim.attempt == 1:
            return {
                "workflow_state": "needs_input",
                "question_digest": "sha256:" + "1" * 64,
                "factory_result_digest": "sha256:" + "2" * 64,
            }
        key = claim.feature_id or claim.workflow_kind
        number, head = self.prs[key]
        return {
            "workflow_state": "verified_for_review",
            "draft": {
                "uri": f"https://github.com/acme/demo/pull/{number}",
                "metadata": {"number": number, "head_commit": head},
            },
        }


class ProgramExecutor:
    def __init__(self):
        self.plans = 0
        self.acceptances = 0

    def plan(self, program):
        self.plans += 1
        plan = {
            "schema_version": "v1alpha1",
            "program": program["program_id"],
            "approved_commit": program["brd"]["approved_commit"],
            "features": [
                {
                    "id": "status-api",
                    "title": "Status API",
                    "summary": "Status.",
                    "acceptance_criteria": ["Pass."],
                    "dependencies": [],
                },
                {
                    "id": "event-store",
                    "title": "Events",
                    "summary": "Events.",
                    "acceptance_criteria": ["Pass."],
                    "dependencies": ["status-api"],
                },
                {
                    "id": "dashboard",
                    "title": "Dashboard",
                    "summary": "Dashboard.",
                    "acceptance_criteria": ["Pass."],
                    "dependencies": ["event-store"],
                },
            ],
        }
        raw = json.dumps(plan, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        return plan, "sha256:" + hashlib.sha256(raw).hexdigest()

    def accept(self, program):
        self.acceptances += 1
        return {
            "schema_version": "v1alpha1",
            "program": program["program_id"],
            "assembled_commit": "9" * 40,
            "features": [feature["id"] for feature in program["features"]],
            "accepted": True,
            "exit_code": 0,
        }


class ProgramForge:
    def __init__(self):
        self.issues = {}
        self.closed = False

    def read_file(self, path, commit):
        assert path == "docs/brd/program-10.md"
        assert commit == "9" * 40
        return b"# BRD: accepted\n"

    def ensure_feature_issue(self, *, program_id, feature, plan_digest):
        number = {"status-api": 101, "event-store": 102, "dashboard": 103}[
            feature["id"]
        ]
        issue = {
            "number": number,
            "html_url": f"https://github.com/acme/demo/issues/{number}",
        }
        self.issues[feature["id"]] = issue
        return issue

    def close(self):
        self.closed = True


def _config(tmp_path: Path) -> ControllerConfig:
    return ControllerConfig(
        repository="https://github.com/acme/demo",
        webhook_secret="webhook-secret",
        github_token="forge-token",
        database=tmp_path / "program.sqlite3",
        result_directory=tmp_path / "results",
    )


def _headers(body: bytes, delivery: str, event: str) -> dict:
    return {
        "x-hub-signature-256": "sha256="
        + hmac.new(b"webhook-secret", body, hashlib.sha256).hexdigest(),
        "x-github-event": event,
        "x-github-delivery": delivery,
        "content-type": "application/json",
    }


def _post(client, document: dict, delivery: str, event: str):
    body = json.dumps(document, separators=(",", ":")).encode()
    return client.post(
        "/webhooks/github", content=body, headers=_headers(body, delivery, event)
    )


def _wait(store: DeliveryStore, predicate):
    deadline = time.time() + 5
    while time.time() < deadline:
        value = store.get_program("program-10")
        if value is not None and predicate(value):
            return value
        time.sleep(0.01)
    raise AssertionError(store.get_program("program-10"))


def _merge(number: int, head: str, merge: str = "9" * 40) -> dict:
    return {
        "action": "closed",
        "repository": {"full_name": "acme/demo"},
        "pull_request": {
            "number": number,
            "html_url": f"https://github.com/acme/demo/pull/{number}",
            "merged": True,
            "merged_at": "2026-08-29T12:00:00Z",
            "merge_commit_sha": merge,
            "merged_by": {"login": "acme", "type": "User"},
            "head": {"sha": head, "repo": {"full_name": "acme/demo"}},
            "base": {"ref": "main", "repo": {"full_name": "acme/demo"}},
        },
    }


def test_full_program_clarification_approval_dependencies_and_acceptance(tmp_path):
    selected = _config(tmp_path)
    store = DeliveryStore(selected.database)
    factory = FactoryExecutor()
    programs = ProgramExecutor()
    forge = ProgramForge()
    controller = DemoController(
        selected,
        store=store,
        executor=factory,
        program_executor=programs,
        program_forge=forge,
    )
    with TestClient(create_app(selected, controller=controller)) as client:
        opened = _post(
            client,
            {
                "action": "opened",
                "repository": {"full_name": "acme/demo"},
                "issue": {
                    "number": 10,
                    "html_url": "https://github.com/acme/demo/issues/10",
                    "title": "Deployment board",
                    "body": "[barista:product-program] [barista:needs-input] Build it.",
                },
            },
            "open-10",
            "issues",
        )
        assert opened.status_code == 202
        _wait(store, lambda value: value["status"] == "brd_needs_input")

        answer = _post(
            client,
            {
                "action": "created",
                "repository": {"full_name": "acme/demo"},
                "issue": {
                    "number": 10,
                    "html_url": "https://github.com/acme/demo/issues/10",
                },
                "comment": {"id": 500, "body": "One container and SQLite."},
                "sender": {"login": "acme", "type": "User"},
            },
            "answer-10",
            "issue_comment",
        )
        assert answer.json()["accepted"] is True
        awaiting = _wait(store, lambda value: value["status"] == "awaiting_brd_merge")
        assert awaiting["brd"]["pr_number"] == 40

        approved = _post(client, _merge(40, "a" * 40), "merge-brd", "pull_request")
        assert approved.json()["kind"] == "brd"
        duplicate_brd = _post(
            client,
            _merge(40, "a" * 40),
            "merge-brd-fresh-duplicate",
            "pull_request",
        )
        assert duplicate_brd.status_code == 202
        first = _wait(
            store,
            lambda value: (
                value["features"] and value["features"][0]["status"] == "awaiting_merge"
            ),
        )
        assert [feature["status"] for feature in first["features"]] == [
            "awaiting_merge",
            "blocked",
            "blocked",
        ]
        assert (
            len([call for call in factory.calls if call.workflow_kind == "feature"])
            == 1
        )
        gated = _post(
            client,
            {
                "action": "opened",
                "repository": {"full_name": "acme/demo"},
                "issue": {
                    "number": 20,
                    "html_url": "https://github.com/acme/demo/issues/20",
                    "body": "<!-- barista-program-feature:v1 program=program-10 feature=status-api plan=sha256:"
                    + "1" * 64
                    + " -->",
                },
            },
            "feature-opened-20",
            "issues",
        )
        assert gated.json()["accepted"] is False
        assert "dependency-gated" in gated.json()["reason"]

        _post(client, _merge(41, "b" * 40), "merge-status", "pull_request")
        _post(
            client,
            _merge(41, "b" * 40),
            "merge-status-fresh-duplicate",
            "pull_request",
        )
        second = _wait(
            store,
            lambda value: value["features"][1]["status"] == "awaiting_merge",
        )
        assert second["features"][2]["status"] == "blocked"

        _post(client, _merge(42, "c" * 40), "merge-events", "pull_request")
        _wait(store, lambda value: value["features"][2]["status"] == "awaiting_merge")
        _post(client, _merge(43, "d" * 40), "merge-dashboard", "pull_request")
        final = _wait(store, lambda value: value["status"] == "accepted")
        endpoint = client.get("/programs/program-10")

    assert endpoint.status_code == 200
    assert all(feature["status"] == "merged" for feature in final["features"])
    assert programs.plans == programs.acceptances == 1
    assert final["acceptance"]["accepted"] is True
    assert forge.closed is False
    controller.close()
    assert forge.closed is True


def test_unauthorized_stale_and_duplicate_merges_are_inert(tmp_path):
    selected = _config(tmp_path)
    store = DeliveryStore(selected.database)
    factory = FactoryExecutor()
    controller = DemoController(
        selected,
        store=store,
        executor=factory,
        program_executor=ProgramExecutor(),
        program_forge=ProgramForge(),
    )
    claim = store.claim(
        delivery_id="open-10",
        repository=selected.repository,
        issue_number=10,
        issue_uri=selected.repository + "/issues/10",
        run_name="github-62924231c5-issue-10-attempt-1",
        workflow_kind="program_brd",
        program_id="program-10",
    )
    store.ensure_program("program-10", claim)
    store.succeed(claim.delivery_id, {"workflow_state": "verified_for_review"})
    store.record_brd_pr(
        "program-10",
        {
            "draft": {
                "uri": selected.repository + "/pull/40",
                "metadata": {"number": 40, "head_commit": "a" * 40},
            }
        },
    )
    with TestClient(create_app(selected, controller=controller)) as client:
        unauthorized = _merge(40, "a" * 40)
        unauthorized["pull_request"]["merged_by"]["login"] = "stranger"
        rejected = _post(client, unauthorized, "merge-x", "pull_request")
        stale = _post(client, _merge(40, "f" * 40), "merge-y", "pull_request")
    assert rejected.json()["accepted"] is False
    assert stale.json()["accepted"] is False
    assert store.get_program("program-10")["status"] == "awaiting_brd_merge"
    controller.close()
