from __future__ import annotations

import json
from types import MappingProxyType

import pytest

from barista_app_sdk import ResolvedApp, RunOperation, Session
from barista_app_sdk import cli


class _Client:
    instance = None

    def __init__(self, config):
        self.config = config
        self.launched = None
        _Client.instance = self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def launch_app_run(self, run, manifest, *, install):
        self.launched = (run, manifest, install)
        operation = RunOperation.from_manifest(manifest, "review")
        return (
            Session(
                id=run.name,
                app=manifest["name"],
                state="creating",
                created_at="2026-08-28T00:00:00Z",
            ),
            operation,
        )


def _resolved() -> ResolvedApp:
    manifest = {
        "schema_version": "v1alpha1",
        "name": "reviewer",
        "version": "1.0.0",
        "workload": {
            "image": "registry.example/reviewer:1.0.0",
            "digest": "sha256:" + "12" * 32,
            "architectures": ["x86_64"],
            "entrypoint": ["reviewer"],
            "working_dir": "/work",
            "readiness": {"type": "none"},
        },
        "runs": {
            "review": {
                "lifecycle": "job",
                "input": {"media_type": "application/json"},
                "bindings": {
                    "workspace": {
                        "kinds": ["sh.barista.git.repository"],
                        "required": True,
                    }
                },
            }
        },
    }
    return ResolvedApp(
        name="reviewer",
        version="1.0.0",
        workload_digest=manifest["workload"]["digest"],
        manifest_digest="sha256:" + "34" * 32,
        manifest=MappingProxyType(manifest),
        source="installed://reviewer",
        source_revision="sha256:" + "34" * 32,
        installed=True,
    )


def test_detached_cli_emits_exact_envelope_and_launch_receipt(tmp_path, monkeypatch, capsys):
    input_path = tmp_path / "input.json"
    input_path.write_text('{"instructions":"review"}')
    envelope_path = tmp_path / "envelope.json"
    monkeypatch.setenv("BARISTA_HOST_API_ENDPOINT", "http://provider.invalid")
    monkeypatch.setattr(cli, "BaristaClient", _Client)
    monkeypatch.setattr(cli, "resolve_app", lambda client, selector, allow_dirty: _resolved())

    code = cli.main(
        [
            "run",
            "--app", "reviewer@1.0.0",
            "--input", str(input_path),
            "--bind",
            'workspace={"kind":"sh.barista.git.repository","uri":"file:///repo","ref":"main"}',
            "--emit-envelope", str(envelope_path),
            "--detach",
        ]
    )

    assert code == 0
    receipt = json.loads(capsys.readouterr().out)
    launched = _Client.instance.launched[0]
    assert receipt["envelope"] == launched.to_document()
    assert receipt["content_id"] == launched.content_id()
    assert envelope_path.read_bytes() == launched.canonical_bytes()
    assert receipt["session_id"].startswith("reviewer-review-")
    assert _Client.instance.launched[2] is False


def test_cli_refuses_cleanup_with_detach_before_provider_access(tmp_path, monkeypatch, capsys):
    input_path = tmp_path / "input.json"
    input_path.write_text("{}")
    monkeypatch.setenv("BARISTA_HOST_API_ENDPOINT", "http://provider.invalid")

    code = cli.main(
        ["run", "--app", "reviewer", "--input", str(input_path), "--detach", "--cleanup"]
    )

    assert code == 2
    assert "cannot be combined" in capsys.readouterr().err


def test_named_bindings_refuse_duplicates_without_echoing_values():
    with pytest.raises(ValueError, match="duplicate binding name"):
        cli._named_json(
            [
                'workspace={"kind":"sh.barista.git.repository","uri":"file:///one"}',
                'workspace={"kind":"sh.barista.git.repository","uri":"file:///two"}',
            ],
            "binding",
        )
