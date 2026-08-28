from __future__ import annotations

import json

import httpx
from barista_github_factory_demo.config import ControllerConfig
import barista_github_factory_demo.live_acceptance as live_acceptance
from barista_github_factory_demo.live_acceptance import run_live_acceptance


class Host:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def list_sessions(self):
        return []


def test_live_acceptance_records_exact_non_secret_evidence(tmp_path, monkeypatch):
    result = {
        "run": "github-demo-issue-12",
        "factory_result_digest": "sha256:" + "d" * 64,
        "base_commit": "a" * 40,
        "patch_digest": "sha256:" + "e" * 64,
        "draft": {
            "uri": "https://github.com/acme/demo/pull/3",
            "metadata": {"head_commit": "b" * 40},
        },
    }

    def post(url, **kwargs):
        assert kwargs["headers"]["Authorization"] == "Bearer runtime-token"
        return httpx.Response(
            201,
            json={"number": 12, "html_url": "https://github.com/acme/demo/issues/12"},
        )

    def get(url, **kwargs):
        if url.endswith("/issues/12"):
            return httpx.Response(200, json={"status": "succeeded", "result": result})
        if url.endswith("/pulls/3"):
            return httpx.Response(
                200,
                json={
                    "draft": True,
                    "base": {"sha": "a" * 40},
                    "head": {"sha": "b" * 40},
                    "body": "verified " + result["patch_digest"],
                },
            )
        raise AssertionError(url)

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(httpx, "get", get)
    output = tmp_path / "evidence.json"
    config = ControllerConfig(
        repository="https://github.com/acme/demo",
        webhook_secret="webhook-secret",
        github_token="runtime-token",
        database=tmp_path / "db",
        result_directory=tmp_path / "results",
    )

    evidence = run_live_acceptance(
        config,
        controller_url="https://controller.example",
        output=output,
        timeout=1,
        client_factory=Host,
    )

    assert evidence["issue"].endswith("/issues/12")
    assert evidence["head_commit"] == "b" * 40
    assert evidence["factory_sessions_absent"] is True
    persisted = output.read_text()
    assert json.loads(persisted) == evidence
    assert "runtime-token" not in persisted
    assert "webhook-secret" not in persisted


def test_live_acceptance_resumes_clarification_as_fresh_attempt(tmp_path, monkeypatch):
    digest = "sha256:" + "c" * 64
    result = {
        "run": "github-demo-issue-12-attempt-2",
        "factory_result_digest": "sha256:" + "d" * 64,
        "base_commit": "a" * 40,
        "patch_digest": "sha256:" + "e" * 64,
        "draft": {
            "uri": "https://github.com/acme/demo/pull/3",
            "metadata": {"head_commit": "b" * 40},
        },
    }
    statuses = iter(
        [
            {
                "status": "awaiting_input",
                "attempt": 1,
                "result": {
                    "question_digest": digest,
                    "comment": "https://github.com/acme/demo/issues/12#issuecomment-4",
                },
            },
            {"status": "succeeded", "attempt": 2, "result": result},
        ]
    )

    def post(url, **kwargs):
        if url.endswith("/issues"):
            return httpx.Response(
                201,
                json={
                    "number": 12,
                    "html_url": "https://github.com/acme/demo/issues/12",
                },
            )
        if url.endswith("/issues/12/comments"):
            return httpx.Response(
                201,
                json={
                    "html_url": "https://github.com/acme/demo/issues/12#issuecomment-5"
                },
            )
        raise AssertionError(url)

    def get(url, **kwargs):
        if url == "https://controller.example/issues/12":
            return httpx.Response(200, json=next(statuses))
        if url.endswith("/issues/12/comments"):
            return httpx.Response(
                200,
                json=[
                    {
                        "html_url": "https://github.com/acme/demo/issues/12#issuecomment-4",
                        "body": f"Question\n\n<!-- barista-factory-question:{digest} -->",
                    }
                ],
            )
        if url.endswith("/pulls/3"):
            return httpx.Response(
                200,
                json={
                    "draft": True,
                    "base": {"sha": "a" * 40},
                    "head": {"sha": "b" * 40},
                    "body": "verified " + result["patch_digest"],
                },
            )
        raise AssertionError(url)

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(httpx, "get", get)
    monkeypatch.setattr(live_acceptance.time, "sleep", lambda _: None)
    config = ControllerConfig(
        repository="https://github.com/acme/demo",
        webhook_secret="webhook-secret",
        github_token="runtime-token",
        database=tmp_path / "db",
        result_directory=tmp_path / "results",
    )

    evidence = run_live_acceptance(
        config,
        controller_url="https://controller.example",
        output=tmp_path / "evidence.json",
        timeout=1,
        client_factory=Host,
        clarify=True,
    )

    assert evidence["run"].endswith("attempt-2")
    assert evidence["clarification"] == {
        "attempt": 1,
        "question_digest": digest,
        "question_comment": "https://github.com/acme/demo/issues/12#issuecomment-4",
        "answer_comment": "https://github.com/acme/demo/issues/12#issuecomment-5",
    }
