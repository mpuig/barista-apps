from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest
from barista_github_factory_demo.bootstrap import (
    SEED_FILES,
    GitHubAdmin,
    setup_demo,
    teardown_demo,
)

ROOT = Path(__file__).resolve().parents[3]


class Admin:
    def __init__(self):
        self.calls = []

    def ensure_repository(self, owner, repository, *, reuse):
        self.calls.append(("repo", owner, repository, reuse))
        return {"full_name": f"{owner}/{repository}", "private": False}

    def ensure_seed(self, owner, repository):
        self.calls.append(("seed", owner, repository))

    def ensure_webhook(self, owner, repository, *, url, secret):
        self.calls.append(("webhook", owner, repository, url, secret))
        return 77

    def delete_webhook(self, owner, repository, hook_id):
        self.calls.append(("delete-hook", owner, repository, hook_id))

    def delete_repository(self, owner, repository):
        self.calls.append(("delete-repo", owner, repository))


class Client:
    def __init__(self, *, fail: bool = False):
        self.installs = []
        self.fail = fail

    def install_app(self, document, *, idempotency_key):
        self.installs.append((document, idempotency_key))
        if self.fail:
            raise RuntimeError("provider unavailable")
        return {"ok": True}


def test_empty_repository_is_seeded_through_contents_before_branch_files(monkeypatch):
    calls = []
    created_readme = False

    def request(method, url, **kwargs):
        nonlocal created_readme
        calls.append((method, url, kwargs))
        if url.endswith("/git/ref/heads/main"):
            return httpx.Response(409, json={"message": "Git Repository is empty."})
        if url.endswith("/repos/acme/demo"):
            return httpx.Response(200, json={"default_branch": "main"})
        if url.endswith("/contents/README.md") and method == "GET":
            if created_readme:
                return httpx.Response(
                    200,
                    json={"content": base64.b64encode(SEED_FILES["README.md"].encode()).decode()},
                )
            return httpx.Response(404, json={"message": "not found"})
        if url.endswith("/contents/README.md") and method == "PUT":
            created_readme = True
            return httpx.Response(201, json={"content": {}})
        if "/contents/" in url and method == "GET":
            return httpx.Response(404, json={"message": "not found"})
        if "/contents/" in url and method == "PUT":
            return httpx.Response(201, json={"content": {}})
        raise AssertionError((method, url, kwargs))

    monkeypatch.setattr(httpx, "request", request)

    GitHubAdmin("bootstrap-token").ensure_seed("acme", "demo")

    assert created_readme
    assert not any("/git/blobs" in url for _, url, _ in calls)
    put_documents = [kwargs["json"] for method, _, kwargs in calls if method == "PUT"]
    assert "branch" not in put_documents[0]
    assert all(document.get("branch") == "main" for document in put_documents[1:])


def test_setup_seeds_webhook_installs_digest_pinned_apps_and_writes_non_secret_state(
    tmp_path,
):
    admin = Admin()
    client = Client()
    state_path = tmp_path / "state.json"

    state = setup_demo(
        token="bootstrap-token",
        owner="acme",
        repository="factory-demo",
        webhook_url="https://demo.example/webhooks/github",
        webhook_secret="webhook-secret",
        factory_manifest=ROOT / "apps/factory/manifest.json",
        factory_name="demo-factory",
        factory_image="ghcr.io/acme/factory:demo",
        factory_digest="sha256:" + "a" * 64,
        worker_manifest=ROOT / "apps/github-issue-worker/manifest.json",
        worker_name="demo-worker",
        worker_image="ghcr.io/acme/worker:demo",
        worker_digest="sha256:" + "b" * 64,
        state_path=state_path,
        reuse=False,
        github=admin,
        client=client,
    )

    assert state["hook_id"] == 77
    assert state["status"] == "ready"
    assert [call[0] for call in admin.calls] == ["repo", "seed", "webhook"]
    assert len(client.installs) == 2
    assert client.installs[0][0]["name"] == "demo-factory"
    assert client.installs[0][0]["workload"]["digest"] == "sha256:" + "a" * 64
    assert client.installs[0][0]["permissions"]["secrets"] == [
        {"name": "BARISTA_HOST_API_TOKEN", "ref": "grant://factory/coordinator"}
    ]
    assert (
        "github"
        not in json.dumps(client.installs[0][0]["permissions"]["secrets"]).lower()
    )
    assert client.installs[1][0]["name"] == "demo-worker"
    persisted = state_path.read_text()
    assert json.loads(persisted) == state
    assert "bootstrap-token" not in persisted
    assert "webhook-secret" not in persisted
    assert state_path.stat().st_mode & 0o777 == 0o600


def test_setup_failure_keeps_teardown_identity_without_secrets(tmp_path):
    admin = Admin()
    state_path = tmp_path / "state.json"

    with pytest.raises(RuntimeError, match="provider unavailable"):
        setup_demo(
            token="bootstrap-token",
            owner="acme",
            repository="factory-demo",
            webhook_url="https://demo.example/webhooks/github",
            webhook_secret="webhook-secret",
            factory_manifest=ROOT / "apps/factory/manifest.json",
            factory_name="demo-factory",
            factory_image="ghcr.io/acme/factory:demo",
            factory_digest="sha256:" + "a" * 64,
            worker_manifest=ROOT / "apps/github-issue-worker/manifest.json",
            worker_name="demo-worker",
            worker_image="ghcr.io/acme/worker:demo",
            worker_digest="sha256:" + "b" * 64,
            state_path=state_path,
            reuse=False,
            github=admin,
            client=Client(fail=True),
        )

    state = json.loads(state_path.read_text())
    assert state["status"] == "bootstrapping"
    assert state["hook_id"] == 77
    assert "bootstrap-token" not in state_path.read_text()
    assert "webhook-secret" not in state_path.read_text()


def test_teardown_requires_explicit_confirmation_before_repository_delete(tmp_path):
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "full_name": "acme/factory-demo",
                "hook_id": 77,
            }
        )
    )
    admin = Admin()

    with pytest.raises(ValueError, match="yes-really-delete"):
        teardown_demo(
            token="token",
            state_path=state_path,
            delete_repository=True,
            confirmed=False,
            github=admin,
        )

    assert admin.calls == []
    assert state_path.exists()


def test_teardown_removes_webhook_and_optionally_repository(tmp_path):
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"full_name": "acme/factory-demo", "hook_id": 77}))
    admin = Admin()

    result = teardown_demo(
        token="token",
        state_path=state_path,
        delete_repository=True,
        confirmed=True,
        github=admin,
    )

    assert result == {"webhook_deleted": True, "repository_deleted": True}
    assert admin.calls == [
        ("delete-hook", "acme", "factory-demo", 77),
        ("delete-repo", "acme", "factory-demo"),
    ]
    assert not state_path.exists()
