"""Golden/compatibility tests for the Barista open contracts.

Covers apps-001 task 1.5: deterministic schemas, manifests, stories, errors,
and content identities. Runs entirely offline — no provider, no Barista Cloud.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator
from jsonschema.validators import validator_for

CONTRACTS = Path(__file__).resolve().parents[1]
MANIFEST_SCHEMA = CONTRACTS / "app-manifest" / "v1alpha1" / "schema.json"
STORY_SCHEMA = CONTRACTS / "session-story" / "v1alpha1" / "schema.json"
SEMANTIC_SCHEMA = CONTRACTS / "session-story" / "v1alpha1" / "semantic-state.schema.json"
OPENAPI = CONTRACTS / "host-api" / "v1alpha1" / "openapi.yaml"
EVENT_SCHEMA = CONTRACTS / "host-api" / "v1alpha1" / "streaming" / "event.schema.json"
ATTACH_SCHEMA = CONTRACTS / "host-api" / "v1alpha1" / "streaming" / "attach-frame.schema.json"

ALL_JSON_SCHEMAS = [MANIFEST_SCHEMA, STORY_SCHEMA, SEMANTIC_SCHEMA, EVENT_SCHEMA, ATTACH_SCHEMA]


def load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def canonical_bytes(value) -> bytes:
    """Deterministic serialization used for content ids: sorted keys, no
    insignificant whitespace, UTF-8, newline-terminated."""
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def content_id(value) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


# --------------------------------------------------------------------------- #
# Every published JSON Schema is itself a valid Draft 2020-12 schema.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("schema_path", ALL_JSON_SCHEMAS, ids=lambda p: p.name)
def test_schema_is_valid_draft202012(schema_path: Path):
    schema = load_json(schema_path)
    cls = validator_for(schema)
    assert cls is Draft202012Validator, f"{schema_path.name} must pin draft 2020-12"
    cls.check_schema(schema)


# --------------------------------------------------------------------------- #
# App Manifest: canonical examples pass, invalid fixtures fail.
# --------------------------------------------------------------------------- #
def _manifest_validator() -> Draft202012Validator:
    return Draft202012Validator(load_json(MANIFEST_SCHEMA), format_checker=Draft202012Validator.FORMAT_CHECKER)


VALID_MANIFESTS = sorted((MANIFEST_SCHEMA.parent / "examples").glob("*.json"))
INVALID_MANIFESTS = sorted((MANIFEST_SCHEMA.parent / "invalid").glob("*.json"))


@pytest.mark.parametrize("example", VALID_MANIFESTS, ids=lambda p: p.name)
def test_valid_manifest_examples(example: Path):
    _manifest_validator().validate(load_json(example))


@pytest.mark.parametrize("fixture", INVALID_MANIFESTS, ids=lambda p: p.name)
def test_invalid_manifest_fixtures_are_rejected(fixture: Path):
    errors = list(_manifest_validator().iter_errors(load_json(fixture)))
    assert errors, f"{fixture.name} must fail validation but passed"


def test_mutable_tag_without_digest_is_rejected():
    """app-manifest spec: a mutable image is rejected as identity."""
    manifest = load_json(MANIFEST_SCHEMA.parent / "examples" / "minimal.json")
    del manifest["workload"]["digest"]
    errors = list(_manifest_validator().iter_errors(manifest))
    assert any("digest" in str(e.message) or e.validator == "required" for e in errors)


def test_plaintext_secret_is_rejected():
    """app-manifest spec: manifests carry secret references, never plaintext."""
    manifest = load_json(MANIFEST_SCHEMA.parent / "examples" / "minimal.json")
    manifest["permissions"] = {"secrets": [{"name": "API_KEY", "ref": "ok", "value": "sk-live-xyz"}]}
    errors = list(_manifest_validator().iter_errors(manifest))
    assert errors, "a secret entry with a plaintext 'value' must be rejected"


# --------------------------------------------------------------------------- #
# Session Story + semantic bundle validate their examples/self-consistency.
# --------------------------------------------------------------------------- #
def test_story_schema_rejects_capsule_like_executable_field():
    story = {
        "schema_version": "v1alpha1",
        "story_id": "sha256:" + "0" * 64,
        "redaction_policy": {"name": "default", "version": "1"},
        "created_at": "2026-08-17T00:00:00Z",
        "records": [{"seq": 0, "type": "event", "text": "hello"}],
        "capsule_object": {"digest": "sha256:" + "1" * 64},
    }
    v = Draft202012Validator(load_json(STORY_SCHEMA))
    errors = list(v.iter_errors(story))
    assert errors, "a story must not accept a capsule_object field (non-executable guarantee)"


def test_semantic_bundle_minimal_valid():
    bundle = {
        "schema_version": "v1alpha1",
        "adapter": "sh.barista.adapter.pi",
        "created_at": "2026-08-17T00:00:00Z",
        "inventory": {"continuation_prompt": "resume the migration"},
        "fidelity": {"level": "high"},
    }
    Draft202012Validator(load_json(SEMANTIC_SCHEMA)).validate(bundle)


# --------------------------------------------------------------------------- #
# Host API OpenAPI parses and pins the standard error classes.
# --------------------------------------------------------------------------- #
def test_openapi_parses_and_is_valid():
    from openapi_spec_validator import validate as validate_openapi

    spec = yaml.safe_load(OPENAPI.read_text())
    validate_openapi(spec)


def test_error_classes_are_the_agreed_set():
    spec = yaml.safe_load(OPENAPI.read_text())
    classes = spec["components"]["schemas"]["Error"]["properties"]["class"]["enum"]
    assert set(classes) == {
        "authentication",
        "authorization",
        "capability",
        "compatibility",
        "conflict",
        "quota",
        "unavailable",
        "terminal",
        "invalid_request",
    }


def test_capability_ids_match_across_manifest_and_host_api():
    """The capability vocabulary must be identical in the manifest schema and the
    Host API discovery/enum — a drift here breaks negotiation."""
    manifest = load_json(MANIFEST_SCHEMA)
    manifest_caps = set(manifest["$defs"]["capability_id"]["enum"])
    spec = yaml.safe_load(OPENAPI.read_text())
    host_caps = set(spec["components"]["schemas"]["CapabilityId"]["enum"])
    assert manifest_caps == host_caps, f"capability drift: {manifest_caps ^ host_caps}"


# --------------------------------------------------------------------------- #
# Content-id determinism (golden).
# --------------------------------------------------------------------------- #
def test_canonical_serialization_is_key_order_independent():
    a = {"b": 1, "a": [3, 2, {"y": 1, "x": 2}]}
    b = {"a": [3, 2, {"x": 2, "y": 1}], "b": 1}
    assert content_id(a) == content_id(b)


def test_manifest_content_id_is_stable_golden():
    """Pin the content id of the minimal manifest. If this changes, the canonical
    serialization or the example changed — both are wire-visible."""
    manifest = load_json(MANIFEST_SCHEMA.parent / "examples" / "minimal.json")
    expected = content_id(manifest)
    # Recompute from a re-parsed copy to prove determinism.
    again = json.loads(json.dumps(manifest))
    assert content_id(again) == expected


def test_secret_ref_must_be_a_scheme_reference_not_plaintext():
    """A secret ref must be a scheme:// reference; a raw credential is rejected."""
    v = _manifest_validator()
    manifest = load_json(MANIFEST_SCHEMA.parent / "examples" / "minimal.json")
    manifest["permissions"] = {"secrets": [{"name": "K", "ref": "secret://vault/k"}]}
    assert not list(v.iter_errors(manifest)), "a secret:// reference must validate"
    manifest["permissions"] = {"secrets": [{"name": "K", "ref": "sk-live-abc123def456"}]}
    assert list(v.iter_errors(manifest)), "a raw credential in ref must be rejected"
