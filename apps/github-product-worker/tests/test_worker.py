from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from barista_app_github_product_worker.worker import (
    ObjectiveError,
    author_brd,
    implement_feature,
    plan_features,
)


def _repo(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    return tmp_path


def _issue(number: int, body: str, *, answers=None) -> dict:
    return {
        "number": number,
        "title": "Deployment board",
        "body": body,
        "state": "open",
        "factory_context": {
            "triage": {
                "schema_version": "v1alpha1",
                "state": "ready",
                "summary": "Build it",
                "acceptance_criteria": ["Pass"],
            },
            "answers": answers or [],
        },
    }


def test_brd_author_records_human_decisions(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    monkeypatch.setenv(
        "BARISTA_OBJECTIVE_URI", "https://github.com/acme/demo/issues/12"
    )
    target = author_brd(
        _issue(
            12,
            "[barista:product-program] Need a status board",
            answers=[{"comment_id": 1, "body": "Use SQLite and one container."}],
        ),
        root,
    )
    text = target.read_text()
    assert text.startswith("# BRD: Deployment board")
    assert "Use SQLite and one container." in text
    assert "one container" in text
    assert (root / "issues/issue-12.md").is_file()


def test_planner_verifies_exact_brd_and_returns_acyclic_chain(tmp_path):
    root = _repo(tmp_path)
    brd = root / "docs/brd/program-12.md"
    brd.parent.mkdir(parents=True)
    brd.write_text("# BRD: demo\n")
    digest = "sha256:" + hashlib.sha256(brd.read_bytes()).hexdigest()
    plan = plan_features(
        {
            "schema_version": "v1alpha1",
            "program": "program-12",
            "approved_commit": "a" * 40,
            "brd_path": "docs/brd/program-12.md",
            "brd_digest": digest,
        },
        root,
    )
    assert [feature["id"] for feature in plan["features"]] == [
        "status-api",
        "event-store",
        "dashboard",
    ]
    assert plan["features"][2]["dependencies"] == ["event-store"]


def test_planner_refuses_changed_brd_bytes(tmp_path):
    root = _repo(tmp_path)
    brd = root / "brd.md"
    brd.write_text("# BRD: changed\n")
    with pytest.raises(ObjectiveError, match="do not match"):
        plan_features(
            {
                "schema_version": "v1alpha1",
                "program": "program-12",
                "approved_commit": "a" * 40,
                "brd_path": "brd.md",
                "brd_digest": "sha256:" + "0" * 64,
            },
            root,
        )


def test_feature_chain_builds_one_container_sqlite_dashboard(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    for number, feature in enumerate(("status-api", "event-store", "dashboard"), 20):
        monkeypatch.setenv(
            "BARISTA_OBJECTIVE_URI", f"https://github.com/acme/demo/issues/{number}"
        )
        selected = implement_feature(
            _issue(
                number,
                "<!-- barista-program-feature:v1 "
                f"program=program-12 feature={feature} plan=sha256:{'1' * 64} -->",
            ),
            root,
        )
        assert selected == feature
    dockerfile = (root / "Dockerfile").read_text()
    assert dockerfile.count("FROM ") == 2
    assert "AS frontend" in dockerfile
    assert 'VOLUME ["/data"]' in dockerfile
    assert (root / "web/src/index.html").is_file()
    assert "api/events" in (root / "app/server.py").read_text()
    assert (root / "product-manifest.json").is_file()
    completed = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_feature_refuses_unknown_or_unmarked_work(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    monkeypatch.setenv(
        "BARISTA_OBJECTIVE_URI", "https://github.com/acme/demo/issues/20"
    )
    with pytest.raises(ObjectiveError, match="marker"):
        implement_feature(_issue(20, "ordinary issue"), root)
    with pytest.raises(ObjectiveError, match="unsupported"):
        implement_feature(
            _issue(
                20,
                "<!-- barista-program-feature:v1 "
                f"program=program-12 feature=evil plan=sha256:{'1' * 64} -->",
            ),
            root,
        )
