"""Pi adapter tests: opaque round-trip, honest fidelity, loud version refusal."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from barista_app_pi import PiAdapter
from barista_app_sdk.adapters import AdapterCompatibilityError

REPO = Path(__file__).resolve().parents[3]
WORKSPACE = "/work/project"


def _write_session(home: Path, version: int) -> bytes:
    enc = "--" + WORKSPACE.strip("/").replace("/", "-") + "--"
    d = home / "sessions" / enc
    d.mkdir(parents=True)
    lines = [
        {"type": "session", "version": version, "id": "019fec49-9d74-7eb1", "cwd": WORKSPACE},
        {"type": "model_change", "id": "eb2b", "provider": "amazon-bedrock", "modelId": "x"},
        {"type": "message", "id": "m1", "role": "user", "text": "hello \u00ff binary-ish"},
    ]
    raw = ("\n".join(json.dumps(x) for x in lines) + "\n").encode()
    (d / "2026-08-17T09-56-47_019fec49.jsonl").write_bytes(raw)
    return raw


def test_detect_and_export_preserves_opaque_native_bytes(tmp_path):
    raw = _write_session(tmp_path, version=3)
    adapter = PiAdapter(home=tmp_path)

    det = adapter.detect(WORKSPACE)
    assert det.detected and det.supported and det.native_version == "3"

    bundle = adapter.export_semantic(WORKSPACE)
    assert bundle.adapter == "sh.barista.adapter.pi"
    # The native transcript bytes are preserved verbatim.
    assert len(bundle.native) == 1
    assert bundle.native[0].data == raw
    assert bundle.native[0].media_type == "application/vnd.pi.session+jsonl"
    # Honest fidelity: semantic, not exact.
    assert bundle.fidelity.level == "high"
    assert "environment" in bundle.fidelity.missing


def test_bundle_document_validates_against_contract_schema(tmp_path):
    _write_session(tmp_path, version=3)
    bundle = PiAdapter(home=tmp_path).export_semantic(WORKSPACE)
    schema = json.loads(
        (REPO / "contracts" / "session-story" / "v1alpha1" / "semantic-state.schema.json").read_text()
    )
    # A valid document proves there are no provider-specific / extra fields.
    Draft202012Validator(schema).validate(bundle.to_document())


def test_continuation_launch_resumes_the_session(tmp_path):
    _write_session(tmp_path, version=3)
    bundle = PiAdapter(home=tmp_path).export_semantic(WORKSPACE)
    launch = PiAdapter(home=tmp_path).continuation_launch(bundle)
    assert launch.command[:2] == ["pi", "--resume"]
    assert launch.command[2] == "019fec49-9d74-7eb1"


def test_unsupported_version_is_refused_loudly(tmp_path):
    _write_session(tmp_path, version=99)
    adapter = PiAdapter(home=tmp_path)
    assert adapter.detect(WORKSPACE).supported is False
    with pytest.raises(AdapterCompatibilityError):
        adapter.export_semantic(WORKSPACE)


def test_manifest_is_valid(tmp_path):
    manifest = json.loads((REPO / "apps" / "pi" / "manifest.json").read_text())
    schema = json.loads(
        (REPO / "contracts" / "app-manifest" / "v1alpha1" / "schema.json").read_text()
    )
    Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER).validate(manifest)
    # Auth is a reference, never a plaintext value.
    for secret in manifest["permissions"]["secrets"]:
        assert secret["ref"].startswith("secret://")
