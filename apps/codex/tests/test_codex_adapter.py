"""Codex adapter tests: opaque round-trip, honest fidelity, loud version refusal."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from barista_app_codex import CodexAdapter
from barista_app_sdk.adapters import AdapterCompatibilityError

REPO = Path(__file__).resolve().parents[3]
WORKSPACE = "/work/project"


def _write_session(home: Path, cli_version="0.53.0") -> bytes:
    d = home / "sessions" / "2025" / "11" / "03"
    d.mkdir(parents=True)
    meta = {
        "timestamp": "2025-11-03T17:45:06.911Z",
        "type": "session_meta",
        "payload": {
            "id": "019a4ad2-9801-7283",
            "cwd": WORKSPACE,
            "originator": "codex_cli_rs",
            "cli_version": cli_version,
        },
    }
    lines = [meta, {"type": "message", "payload": {"role": "user", "content": "hi \u00ff"}}]
    raw = ("\n".join(json.dumps(x) for x in lines) + "\n").encode()
    (d / "rollout-2025-11-03T18-45-06-019a4ad2.jsonl").write_bytes(raw)
    return raw


def test_detect_and_export_preserves_opaque_native_bytes(tmp_path):
    raw = _write_session(tmp_path)
    adapter = CodexAdapter(home=tmp_path)
    det = adapter.detect(WORKSPACE)
    assert det.detected and det.supported and det.native_version == "0.53.0"

    bundle = adapter.export_semantic(WORKSPACE)
    assert bundle.native[0].data == raw
    assert bundle.native[0].media_type == "application/vnd.codex.rollout+jsonl"
    assert bundle.fidelity.level == "high"


def test_bundle_document_validates_against_contract_schema(tmp_path):
    _write_session(tmp_path)
    bundle = CodexAdapter(home=tmp_path).export_semantic(WORKSPACE)
    schema = json.loads(
        (REPO / "contracts" / "session-story" / "v1alpha1" / "semantic-state.schema.json").read_text()
    )
    Draft202012Validator(schema).validate(bundle.to_document())


def test_continuation_resumes_the_session(tmp_path):
    _write_session(tmp_path)
    bundle = CodexAdapter(home=tmp_path).export_semantic(WORKSPACE)
    launch = CodexAdapter(home=tmp_path).continuation_launch(bundle)
    assert launch.command == ["codex", "resume", "019a4ad2-9801-7283"]


def test_unsupported_version_is_refused_loudly(tmp_path):
    _write_session(tmp_path, cli_version="999.0.0")
    adapter = CodexAdapter(home=tmp_path)
    assert adapter.detect(WORKSPACE).supported is False
    with pytest.raises(AdapterCompatibilityError):
        adapter.export_semantic(WORKSPACE)


def test_manifest_is_valid(tmp_path):
    manifest = json.loads((REPO / "apps" / "codex" / "manifest.json").read_text())
    schema = json.loads((REPO / "contracts" / "app-manifest" / "v1alpha1" / "schema.json").read_text())
    Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER).validate(manifest)
    for secret in manifest["permissions"]["secrets"]:
        assert secret["ref"].startswith("secret://")
