from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from barista_app_factory.product_program import (
    FeaturePlan,
    FeaturePlanError,
    execute_feature_plan,
    execute_program_acceptance,
)
from barista_app_sdk import AppRun, ExecHandle
from barista_app_sdk.content import canonical_bytes
from barista_app_sdk.errors import InvalidRequestError

from test_software_change import WorkerClient, _result_path


def _plan() -> dict:
    return {
        "schema_version": "v1alpha1",
        "program": "program-12",
        "approved_commit": "a" * 40,
        "features": [
            {
                "id": "api",
                "title": "API",
                "summary": "Build API.",
                "acceptance_criteria": ["API passes."],
                "dependencies": [],
            },
            {
                "id": "web",
                "title": "Web",
                "summary": "Build web.",
                "acceptance_criteria": ["Web passes."],
                "dependencies": ["api"],
            },
        ],
    }


def test_feature_plan_is_closed_canonical_and_acyclic():
    parsed = FeaturePlan.parse_bytes(canonical_bytes(_plan()))
    assert parsed.program == "program-12"
    assert [feature.id for feature in parsed.features] == ["api", "web"]
    assert parsed.features[1].dependencies == ("api",)
    assert parsed.content_id().startswith("sha256:")


def test_feature_plan_refuses_cycle_unknown_fields_and_noncanonical_bytes():
    cycle = _plan()
    cycle["features"][0]["dependencies"] = ["web"]
    with pytest.raises(FeaturePlanError, match="cycle"):
        FeaturePlan.parse_bytes(canonical_bytes(cycle))
    unknown = _plan()
    unknown["authority"] = {"command": ["publish"]}
    with pytest.raises(FeaturePlanError, match="fields"):
        FeaturePlan.parse_bytes(canonical_bytes(unknown))
    with pytest.raises(FeaturePlanError, match="canonical"):
        FeaturePlan.parse_bytes(json.dumps(_plan()).encode())


def test_feature_plan_refuses_duplicate_and_unknown_dependencies():
    duplicate = _plan()
    duplicate["features"][1]["id"] = "api"
    with pytest.raises(FeaturePlanError, match="invalid"):
        FeaturePlan.parse_bytes(canonical_bytes(duplicate))
    unknown = _plan()
    unknown["features"][1]["dependencies"] = ["missing"]
    with pytest.raises(FeaturePlanError, match="unknown"):
        FeaturePlan.parse_bytes(canonical_bytes(unknown))


class PlanClient(WorkerClient):
    def __init__(self, plan: bytes):
        super().__init__({})
        self.plan = plan

    def exec(self, session_id, command, **kwargs):
        if "planner" in session_id:
            with self._lock:
                self._counter += 1
                operation_id = f"operation-{self._counter}"
            output = b""
            if (
                command[:2] == ["sh", "-c"]
                and "barista-feature-plan.json" in command[2]
            ):
                output = (
                    f"{len(self.plan)}\n{hashlib.sha256(self.plan).hexdigest()}  "
                    "/tmp/barista-feature-plan.json\n"
                ).encode()
            elif command[:1] == ["dd"]:
                block = int(next(arg[3:] for arg in command if arg.startswith("bs=")))
                index = int(next(arg[5:] for arg in command if arg.startswith("skip=")))
                output = self.plan[index * block : (index + 1) * block]
            self.operations[operation_id] = (0, output)
            return ExecHandle(operation_id=operation_id, event_cursor=operation_id)
        return super().exec(session_id, command, **kwargs)


def _repository(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "-C", str(root), "init", "-q", "-b", "main"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    brd = root / "docs/brd/program-8.md"
    brd.parent.mkdir(parents=True)
    brd.write_text("# BRD: test\n")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], check=True)
    commit = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return root, commit


def test_feature_plan_operation_runs_isolated_planner_and_returns_verified_artifact(
    tmp_path, monkeypatch
):
    repository, commit = _repository(tmp_path)
    document = _plan()
    document["approved_commit"] = commit
    raw = canonical_bytes(document)
    client = PlanClient(raw)
    _result_path(tmp_path, monkeypatch)
    monkeypatch.setenv("BARISTA_APP_SESSION_ID", "owner")
    run = AppRun.parse(
        {
            "schema_version": "v1alpha1",
            "name": "program-8-feature-plan",
            "app": "factory@0.1.0",
            "operation": "feature-plan",
            "input": {
                "media_type": "application/json",
                "value": {
                    "planner_app": "planner",
                    "planner": {"command": ["planner"]},
                    "program": "program-12",
                    "approved_commit": commit,
                    "brd_path": "docs/brd/program-8.md",
                    "brd_digest": "sha256:"
                    + hashlib.sha256(b"# BRD: test\n").hexdigest(),
                },
            },
            "bindings": {
                "workspace": {
                    "kind": "sh.barista.git.repository",
                    "uri": repository.as_uri(),
                    "ref": "main",
                }
            },
        }
    )
    result = execute_feature_plan(
        client, run, work_root=tmp_path / "program-runs"
    ).to_document()
    assert result["state"] == "succeeded"
    assert (
        result["outputs"]["plan"]["digest"] == FeaturePlan.parse_bytes(raw).content_id()
    )
    assert client.deleted == [run.name + "-planner"]


def test_feature_plan_operation_rejects_changed_approved_brd(tmp_path, monkeypatch):
    repository, commit = _repository(tmp_path)
    document = _plan()
    document["approved_commit"] = commit
    client = PlanClient(canonical_bytes(document))
    _result_path(tmp_path, monkeypatch)
    monkeypatch.setenv("BARISTA_APP_SESSION_ID", "owner")
    run = AppRun.parse(
        {
            "schema_version": "v1alpha1",
            "name": "program-8-bad-brd",
            "app": "factory@0.1.0",
            "operation": "feature-plan",
            "input": {
                "media_type": "application/json",
                "value": {
                    "planner_app": "planner",
                    "planner": {"command": ["planner"]},
                    "program": "program-12",
                    "approved_commit": commit,
                    "brd_path": "docs/brd/program-8.md",
                    "brd_digest": "sha256:" + "1" * 64,
                },
            },
            "bindings": {
                "workspace": {
                    "kind": "sh.barista.git.repository",
                    "uri": repository.as_uri(),
                    "ref": commit,
                }
            },
        }
    )
    with pytest.raises(InvalidRequestError, match="BRD bytes changed"):
        execute_feature_plan(client, run, work_root=tmp_path / "bad-brd-runs")
    assert client.deleted == []


def test_program_acceptance_strips_authority_and_binds_exact_commit(
    tmp_path, monkeypatch
):
    repository, commit = _repository(tmp_path)
    client = WorkerClient({})
    _result_path(tmp_path, monkeypatch)
    monkeypatch.setenv("BARISTA_APP_SESSION_ID", "owner")
    monkeypatch.setenv("BARISTA_HOST_API_TOKEN", "must-not-enter-check")
    check = (
        "import os\n"
        "from pathlib import Path\n"
        "assert 'BARISTA_HOST_API_TOKEN' not in os.environ\n"
        "assert Path('docs/brd/program-8.md').is_file()\n"
    )
    run = AppRun.parse(
        {
            "schema_version": "v1alpha1",
            "name": "program-8-acceptance",
            "app": "factory@0.1.0",
            "operation": "program-acceptance",
            "input": {
                "media_type": "application/json",
                "value": {
                    "program": "program-8",
                    "assembled_commit": commit,
                    "features": ["api"],
                    "acceptance": {
                        "command": [sys.executable, ".barista/check.py"],
                        "files": {".barista/check.py": check},
                    },
                },
            },
            "bindings": {
                "workspace": {
                    "kind": "sh.barista.git.repository",
                    "uri": repository.as_uri(),
                    "ref": "main",
                }
            },
        }
    )
    result = execute_program_acceptance(
        client, run, work_root=tmp_path / "program-runs"
    ).to_document()
    assert result["state"] == "succeeded"
    report = json.loads(
        Path(result["outputs"]["result"]["uri"].removeprefix("file://")).read_bytes()
    )
    assert report["accepted"] is True
    assert report["assembled_commit"] == commit
