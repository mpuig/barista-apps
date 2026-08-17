"""Claude Code adapter tests: opaque round-trip, honest fidelity, loud refusal."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from barista_app_claude import ClaudeAdapter
from barista_app_sdk.adapters import AdapterCompatibilityError

REPO = Path(__file__).resolve().parents[3]
WORKSPACE = "/work/project"


def _write_session(home: Path, version=None) -> bytes:
    d = home / "projects" / WORKSPACE.replace("/", "-")
    d.mkdir(parents=True)
    first = {"type": "summary", "sessionId": "b0f5692f-528c-43c6", "aiTitle": "t"}
    if version is not None:
        first["version"] = version
    lines = [first, {"type": "user", "message": {"role": "user", "content": "hi \u00ff"}}]
    raw = ("\n".join(json.dumps(x) for x in lines) + "\n").encode()
    (d / "b0f5692f-528c-43c6.jsonl").write_bytes(raw)
    return raw


def test_detect_and_export_preserves_opaque_native_bytes(tmp_path):
    raw = _write_session(tmp_path)
    adapter = ClaudeAdapter(home=tmp_path)
    det = adapter.detect(WORKSPACE)
    assert det.detected and det.supported

    bundle = adapter.export_semantic(WORKSPACE)
    assert bundle.native[0].data == raw
    assert bundle.native[0].media_type == "application/vnd.claude-code.transcript+jsonl"
    assert bundle.fidelity.level == "high"


def test_bundle_document_validates_against_contract_schema(tmp_path):
    _write_session(tmp_path)
    bundle = ClaudeAdapter(home=tmp_path).export_semantic(WORKSPACE)
    schema = json.loads(
        (REPO / "contracts" / "session-story" / "v1alpha1" / "semantic-state.schema.json").read_text()
    )
    Draft202012Validator(schema).validate(bundle.to_document())


def test_continuation_resumes_the_session(tmp_path):
    _write_session(tmp_path)
    bundle = ClaudeAdapter(home=tmp_path).export_semantic(WORKSPACE)
    launch = ClaudeAdapter(home=tmp_path).continuation_launch(bundle)
    assert launch.command == ["claude", "--resume", "b0f5692f-528c-43c6"]


def test_unsupported_version_is_refused_loudly(tmp_path):
    _write_session(tmp_path, version=99)
    adapter = ClaudeAdapter(home=tmp_path)
    assert adapter.detect(WORKSPACE).supported is False
    with pytest.raises(AdapterCompatibilityError):
        adapter.export_semantic(WORKSPACE)


def test_manifest_is_valid(tmp_path):
    manifest = json.loads((REPO / "apps" / "claude" / "manifest.json").read_text())
    schema = json.loads((REPO / "contracts" / "app-manifest" / "v1alpha1" / "schema.json").read_text())
    Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER).validate(manifest)
    for secret in manifest["permissions"]["secrets"]:
        assert secret["ref"].startswith("secret://")
