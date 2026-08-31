from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import barista_managed_smoke as smoke
import pytest


def test_default_profile_emits_machine_readable_bounded_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    output = tmp_path / "report.json"
    monkeypatch.setenv("BARISTA_HOST_API_ENDPOINT", "https://provider.example")
    monkeypatch.setenv("BARISTA_HOST_API_TOKEN", "secret-never-reported")
    monkeypatch.setattr(
        smoke,
        "_pytest_step",
        lambda nodes, timeout: {"tests": nodes, "output_tail": "1 passed"},
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["barista-managed-smoke", "--output", str(output)],
    )

    assert smoke.main() == 0
    report = json.loads(output.read_text())
    assert report["state"] == "passed"
    assert [step["name"] for step in report["steps"]] == [
        "managed-lifecycle",
        "factory-dependency-mission",
    ]
    assert "secret-never-reported" not in output.read_text()
    assert json.loads(capsys.readouterr().out) == report


def test_failed_step_is_recorded_and_returns_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setenv("BARISTA_HOST_API_ENDPOINT", "https://provider.example")
    monkeypatch.setenv("BARISTA_HOST_API_TOKEN", "token")

    def fail(_nodes, _timeout):
        raise smoke.SmokeFailure("first exec never became ready")

    monkeypatch.setattr(smoke, "_pytest_step", fail)
    monkeypatch.setattr(sys, "argv", ["barista-managed-smoke"])

    assert smoke.main() == 1
    report = json.loads(capsys.readouterr().out)
    assert report["state"] == "failed"
    assert report["steps"][0]["state"] == "failed"
    assert "first exec" in report["steps"][0]["detail"]["error"]


def test_pytest_step_uses_argv_not_a_shell_and_bounds_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}

    def run(command, **kwargs):
        captured.update({"command": command, **kwargs})
        return subprocess.CompletedProcess(command, 0, "x" * 20_000, "")

    monkeypatch.setattr(subprocess, "run", run)
    detail = smoke._pytest_step(["tests/test_example.py::test_case"], 12)
    assert "shell" not in captured
    assert isinstance(captured["command"], list)
    assert captured["timeout"] == 12
    assert len(detail["output_tail"]) == smoke.MAX_OUTPUT_CHARS


def test_pytest_step_refuses_a_skipped_managed_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            "SKIPPED [1] no managed capability\n1 skipped in 0.1s\n",
            "",
        )

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(smoke.SmokeFailure, match="was skipped"):
        smoke._pytest_step(["tests/test_example.py::test_case"], 12)


def test_model_profile_requires_provider_side_app_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BARISTA_MANAGED_SMOKE_AGENT_CHECKS", raising=False)
    with pytest.raises(ValueError, match="secret references"):
        smoke._agent_checks()

    monkeypatch.setenv(
        "BARISTA_MANAGED_SMOKE_AGENT_CHECKS",
        json.dumps(
            [
                {
                    "name": "pi",
                    "app": "pi",
                    "command": ["pi", "--print", "reply PI_OK"],
                    "expected": "PI_OK",
                }
            ]
        ),
    )
    assert smoke._agent_checks()[0]["app"] == "pi"


def test_url_checks_require_named_http_urls() -> None:
    assert smoke._url_checks(["cloud=https://beta.example/healthz"]) == [
        ("cloud", "https://beta.example/healthz")
    ]
    with pytest.raises(ValueError):
        smoke._url_checks(["https://beta.example"])
    with pytest.raises(ValueError, match="credential-free"):
        smoke._check_url("bad", "https://user:password@beta.example/")
