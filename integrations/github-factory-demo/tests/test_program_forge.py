from __future__ import annotations

import json

import httpx
import pytest

from barista_app_sdk.errors import ResultIntegrityError
from barista_github_factory_demo.program import GitHubProgramForge


def _feature() -> dict:
    return {
        "id": "status-api",
        "title": "Status API",
        "summary": "Add status.",
        "acceptance_criteria": ["Health passes."],
        "dependencies": [],
    }


def test_approved_brd_bytes_are_bounded_and_decoded():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/contents/docs/brd/program-7.md")
        assert request.url.params["ref"] == "a" * 40
        assert request.headers["accept"] == "application/vnd.github.raw+json"
        return httpx.Response(200, content=b"# BRD: accepted\n")

    forge = GitHubProgramForge(
        token="forge-token",
        repository="https://github.com/acme/demo",
        client=httpx.Client(
            base_url="https://api.github.test",
            transport=httpx.MockTransport(handler),
        ),
    )
    assert forge.read_file("docs/brd/program-7.md", "a" * 40) == b"# BRD: accepted\n"


def test_feature_issue_delivery_reuses_marker_or_creates_once():
    requests: list[tuple[str, dict | None]] = []
    existing = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal existing
        document = json.loads(request.content) if request.content else None
        requests.append((request.method, document))
        marker = (
            "<!-- barista-program-feature:v1 program=program-7 "
            f"feature=status-api plan=sha256:{'1' * 64} -->"
        )
        if request.method == "GET":
            return httpx.Response(
                200,
                json=(
                    [
                        {
                            "number": 20,
                            "html_url": "https://github.com/acme/demo/issues/20",
                            "body": marker,
                        }
                    ]
                    if existing
                    else []
                ),
            )
        existing = True
        return httpx.Response(
            201,
            json={
                "number": 20,
                "html_url": "https://github.com/acme/demo/issues/20",
                "body": document["body"],
            },
        )

    forge = GitHubProgramForge(
        token="forge-token",
        repository="https://github.com/acme/demo",
        client=httpx.Client(
            base_url="https://api.github.test",
            transport=httpx.MockTransport(handler),
        ),
    )
    first = forge.ensure_feature_issue(
        program_id="program-7",
        feature=_feature(),
        plan_digest="sha256:" + "1" * 64,
    )
    second = forge.ensure_feature_issue(
        program_id="program-7",
        feature=_feature(),
        plan_digest="sha256:" + "1" * 64,
    )
    assert first["html_url"] == second["html_url"]
    assert [method for method, _ in requests].count("POST") == 1
    assert "forge-token" not in repr(requests)


def test_demo_issue_launch_reuses_exact_marker_and_closes_by_number():
    requests: list[tuple[str, str, dict | None]] = []
    issue = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal issue
        document = json.loads(request.content) if request.content else None
        requests.append((request.method, request.url.path, document))
        if request.method == "GET":
            return httpx.Response(200, json=[issue] if issue else [])
        if request.method == "POST":
            issue = {
                "number": 22,
                "html_url": "https://github.com/acme/demo/issues/22",
                "body": document["body"],
            }
            return httpx.Response(201, json=issue)
        assert request.method == "PATCH"
        assert document == {"state": "closed"}
        return httpx.Response(200, json={**issue, "state": "closed"})

    forge = GitHubProgramForge(
        token="forge-token",
        repository="https://github.com/acme/demo",
        client=httpx.Client(
            base_url="https://api.github.test",
            transport=httpx.MockTransport(handler),
        ),
    )
    first = forge.ensure_demo_issue("demo-12345678")
    second = forge.ensure_demo_issue("demo-12345678")
    latest = forge.latest_demo_issue()
    forge.close_demo_issue(22)

    assert first == second
    assert latest["demo_idempotency_key"] == "demo-12345678"
    assert [method for method, _, _ in requests].count("POST") == 1
    assert [method for method, _, _ in requests].count("PATCH") == 1
    assert "[barista:product-program]" in first["body"]
    assert "[barista:needs-input]" in first["body"]
    assert "forge-token" not in repr(requests)


def test_duplicate_feature_markers_are_integrity_failure():
    marker = (
        "<!-- barista-program-feature:v1 program=program-7 "
        f"feature=status-api plan=sha256:{'1' * 64} -->"
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {"number": 1, "body": marker},
                {"number": 2, "body": marker},
            ],
        )

    forge = GitHubProgramForge(
        token="forge-token",
        repository="https://github.com/acme/demo",
        client=httpx.Client(
            base_url="https://api.github.test",
            transport=httpx.MockTransport(handler),
        ),
    )
    with pytest.raises(ResultIntegrityError, match="duplicated"):
        forge.ensure_feature_issue(
            program_id="program-7",
            feature=_feature(),
            plan_digest="sha256:" + "1" * 64,
        )
