"""Session Story tests: determinism, redaction + fail-closed, non-executability,
pseudonymization, signatures, schema validity, and a script-free viewer.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from barista_app_story import (
    RedactionError,
    Source,
    StoryBuilder,
    StoryError,
    record_digest,
    render_html,
)

REPO = Path(__file__).resolve().parents[3]
CREATED = "2026-08-17T00:00:00Z"


def _records():
    return [
        {"type": "decision", "time": "2026-08-17T00:00:01Z", "text": "Chose plan A"},
        {"type": "command", "time": "2026-08-17T00:00:02Z", "text": "run: make test"},
        {"type": "artifact_ref", "time": "2026-08-17T00:00:03Z",
         "artifact": {"name": "out.txt", "digest": "sha256:" + "ab" * 32, "media_type": "text/plain"}},
    ]


def _schema():
    return json.loads((REPO / "contracts" / "session-story" / "v1alpha1" / "schema.json").read_text())


def test_build_validates_against_contract_schema():
    bundle = StoryBuilder().build(_records(), created_at=CREATED, title="t", source=Source(app="pi"))
    Draft202012Validator(_schema(), format_checker=Draft202012Validator.FORMAT_CHECKER).validate(bundle)
    assert bundle["story_id"].startswith("sha256:")


def test_story_id_is_deterministic():
    a = StoryBuilder().build(_records(), created_at=CREATED, title="t")
    b = StoryBuilder().build(_records(), created_at=CREATED, title="t")
    assert a["story_id"] == b["story_id"]
    # Same records + same policy + same created_at -> identical canonical bytes.
    assert a == b


def test_redaction_removes_secrets_and_reports_categories():
    records = [
        {"type": "command", "text": "export OPENAI_API_KEY=sk-abcdefghijklmnop1234"},
        {"type": "event", "text": "email me at dev@example.com"},
    ]
    bundle = StoryBuilder().build(records, created_at=CREATED)
    blob = json.dumps(bundle)
    assert "sk-abcdefghijklmnop1234" not in blob
    assert "dev@example.com" not in blob
    cats = {r["category"] for r in bundle["removed"]}
    assert "secret" in cats and "pii" in cats


def test_unknown_media_fails_closed():
    records = [{"type": "artifact_ref", "artifact": {"digest": "sha256:" + "cd" * 32, "media_type": "application/x-evil"}}]
    with pytest.raises(RedactionError):
        StoryBuilder().build(records, created_at=CREATED)


def test_residual_secret_in_data_fails_closed():
    # A secret hidden in inert 'data' must also block publication.
    records = [{"type": "event", "data": {"note": "token sk-abcdefghijklmnop1234"}}]
    with pytest.raises(RedactionError):
        StoryBuilder().build(records, created_at=CREATED)


def test_story_is_non_executable():
    schema = _schema()
    bundle = StoryBuilder().build(_records(), created_at=CREATED)
    # No executable/grant/capsule fields anywhere.
    blob = json.dumps(bundle)
    for forbidden in ("capsule_object", "\"grant\"", "bearer", "writable"):
        assert forbidden not in blob
    # Building a story that tries to smuggle a capsule object is refused.
    with pytest.raises(StoryError):
        StoryBuilder().build([{"type": "event", "data": {"capsule_object": {"digest": "x"}}}], created_at=CREATED)


def test_pseudonymize_mints_new_id_but_preserves_record_digests():
    bundle = StoryBuilder().build(
        [{"type": "event", "seq": 0, "text": "session my-private-name started"}],
        created_at=CREATED, title="my-private-name run",
    )
    before = [record_digest(r) for r in bundle["records"] if "my-private" not in json.dumps(r)]
    pseudo = StoryBuilder.pseudonymize(bundle, {"my-private-name": "pub-abc123"})
    assert pseudo["story_id"] != bundle["story_id"]
    assert "my-private-name" not in json.dumps(pseudo)
    # Records that did not mention the private name keep their digests.
    after = [record_digest(r) for r in pseudo["records"] if "pub-abc123" not in json.dumps(r)]
    assert before == after


def test_sign_and_verify_roundtrip():
    bundle = StoryBuilder().build(_records(), created_at=CREATED)
    key = b"test-key"
    signer = lambda payload: hmac.new(key, payload, hashlib.sha256).hexdigest()
    verifier = lambda payload, value: hmac.compare_digest(value, hmac.new(key, payload, hashlib.sha256).hexdigest())
    signed = StoryBuilder.sign(bundle, signer, algorithm="hmac-sha256", key_id="k1")
    assert StoryBuilder.verify(signed, verifier) is True
    Draft202012Validator(_schema(), format_checker=Draft202012Validator.FORMAT_CHECKER).validate(signed)


def test_viewer_has_no_scripts():
    bundle = StoryBuilder().build(_records(), created_at=CREATED, title="t")
    html = render_html(bundle)
    assert "<script" not in html.lower()
    assert "story id" in html and bundle["story_id"] in html


def test_manifest_is_valid():
    manifest = json.loads((REPO / "apps" / "story" / "manifest.json").read_text())
    schema = json.loads((REPO / "contracts" / "app-manifest" / "v1alpha1" / "schema.json").read_text())
    Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER).validate(manifest)
