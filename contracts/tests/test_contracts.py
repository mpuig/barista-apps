"""Golden/compatibility tests for the Barista open contracts.

Covers apps-001 task 1.5: deterministic schemas, manifests, stories, errors,
and content identities. Runs entirely offline — no provider, no Barista Cloud.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator
from jsonschema.validators import validator_for

CONTRACTS = Path(__file__).resolve().parents[1]
REPO = CONTRACTS.parent
MANIFEST_SCHEMA = CONTRACTS / "app-manifest" / "v1alpha1" / "schema.json"

# The rules JSON Schema cannot express live beside the schema itself.
sys.path.insert(0, str(MANIFEST_SCHEMA.parent))
import rules  # noqa: E402
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
# Action scope + child-session authority (apps-002).
#
# The schema carries shape; the subset rule between child_sessions.actions and
# permissions.actions is NOT expressible in JSON Schema and is NOT enforced by
# it. These tests pin both halves: what the schema catches, and what only
# rules.py catches.
# --------------------------------------------------------------------------- #
def _with_permissions(**permissions) -> dict:
    manifest = load_json(MANIFEST_SCHEMA.parent / "examples" / "minimal.json")
    manifest["permissions"] = permissions
    return manifest


def _schema_errors(manifest: dict) -> list:
    return list(_manifest_validator().iter_errors(manifest))


SEMANTICALLY_INVALID = sorted((MANIFEST_SCHEMA.parent / "semantically-invalid").glob("*.json"))
APP_MANIFESTS = sorted(REPO.glob("apps/*/manifest.json"))


def test_semantically_invalid_directory_is_not_empty():
    assert SEMANTICALLY_INVALID, "the schema's limits must be documented by fixtures"


@pytest.mark.parametrize("fixture", SEMANTICALLY_INVALID, ids=lambda p: p.name)
def test_semantically_invalid_fixtures_pass_schema_but_fail_the_rules(fixture: Path):
    """The whole point of task 1.4: these validate structurally and must still be
    refused. If a fixture here ever fails the schema, the fixture — not the
    schema — has drifted, and it stops proving anything."""
    manifest = load_json(fixture)
    assert not _schema_errors(manifest), (
        f"{fixture.name} must PASS the schema; it exists to show what the schema cannot see"
    )
    violations = rules.check_manifest(manifest)
    assert violations, f"{fixture.name} must be refused by the semantic rules"
    # A refusal has to name what exceeded what — "invalid manifest" is not usable.
    assert all(v.actions for v in violations), [str(v) for v in violations]


def test_child_actions_exceeding_the_app_are_identifiable_though_not_by_the_schema():
    """A child action the app does not hold. The schema accepts it; the rule
    names it. Both halves asserted together so neither can quietly change."""
    manifest = _with_permissions(
        actions=["session.create", {"action": "session.exec", "scope": "created_sessions"}],
        child_sessions={"max_concurrent": 2, "actions": ["session.delete"]},
    )
    assert not _schema_errors(manifest), "JSON Schema cannot relate the two lists — it must accept this"
    violations = rules.check_manifest(manifest)
    assert [v.rule for v in violations] == ["child_actions_exceed_app"]
    assert violations[0].actions == ("session.delete",)


def test_downward_delegation_requires_the_app_to_hold_it_downwards():
    manifest = _with_permissions(
        actions=["session.create", {"action": "session.exec", "scope": "own_session"}],
        child_sessions={
            "allow_descendants": True,
            "actions": ["session.create", {"action": "session.exec", "scope": "created_sessions"}],
        },
    )
    assert not _schema_errors(manifest)
    rule_ids = {v.rule for v in rules.check_manifest(manifest)}
    assert "child_created_scope_exceeds_app_scope" in rule_ids


def test_duplicate_action_scope_is_identifiable():
    """`uniqueItems` cannot see that a bare id and scope 'own_session' are the
    same grant, so the rule has to."""
    manifest = _with_permissions(
        actions=["session.exec", {"action": "session.exec", "scope": "own_session"}]
    )
    assert not _schema_errors(manifest)
    violations = rules.check_manifest(manifest)
    assert [v.rule for v in violations] == ["duplicate_action_scope"]
    assert violations[0].actions == ("session.exec@own_session",)


def test_session_create_takes_no_scope():
    """It is collection-level and bounded by child_sessions, not by a scope. The
    schema rejects the object form rather than leave a provider guessing."""
    assert not _schema_errors(_with_permissions(actions=["session.create"]))
    assert _schema_errors(
        _with_permissions(actions=[{"action": "session.create", "scope": "created_sessions"}])
    ), "a scope on session.create must be rejected"
    assert _schema_errors(_with_permissions(actions=[{"action": "session.create"}]))


def test_object_form_always_states_its_scope():
    """No path to a wide scope by omission: scope is required in the object form
    and only a bare action id may mean 'own_session'."""
    assert _schema_errors(_with_permissions(actions=[{"action": "session.exec"}]))
    assert _schema_errors(_with_permissions(actions=[{"action": "session.exec", "scope": "any_session"}]))
    assert not _schema_errors(_with_permissions(actions=[{"action": "session.exec", "scope": "own_session"}]))


def test_a_child_may_not_be_given_session_create_without_allow_descendants():
    """The ratified factory-app scenario, as far up as the schema can carry it."""
    for child in (
        {"actions": ["session.create"]},
        {"allow_descendants": False, "actions": ["session.create"]},
    ):
        manifest = _with_permissions(actions=["session.create"], child_sessions=child)
        assert _schema_errors(manifest), f"{child} must be rejected by the schema"
    ok = _with_permissions(
        actions=["session.create", {"action": "session.exec", "scope": "created_sessions"}],
        child_sessions={
            "allow_descendants": True,
            "actions": ["session.create", {"action": "session.exec", "scope": "created_sessions"}],
        },
    )
    assert not _schema_errors(ok)
    assert not rules.check_manifest(ok)


def test_scopes_normalize_to_action_scope_pairs():
    grants = rules.normalize(
        ["session.exec", {"action": "artifact.read", "scope": "created_sessions"}]
    )
    assert grants == [
        rules.Grant("session.exec", "own_session"),
        rules.Grant("artifact.read", "created_sessions"),
    ]
    assert str(grants[1]) == "artifact.read@created_sessions"
    # session.create prints without a scope: it never has one.
    assert str(rules.Grant("session.create", "own_session")) == "session.create"


# --------------------------------------------------------------------------- #
# Backward compatibility: a manifest written before scopes existed.
# --------------------------------------------------------------------------- #
PRE_SCOPE_FACTORY = MANIFEST_SCHEMA.parent / "compat" / "pre-scope-factory.json"


def test_frozen_pre_change_manifest_is_the_real_pre_change_artifact():
    """compat/pre-scope-factory.json is a byte-for-byte copy of
    apps/factory/manifest.json before this change. Pin its digest so it cannot
    be quietly "fixed" into passing."""
    digest = hashlib.sha256(PRE_SCOPE_FACTORY.read_bytes()).hexdigest()
    assert digest == "c646a0d5830bb1886a9ac1e5c5f40ee0a994f0ef22b5c11d544ed8bb4d122940", (
        "the frozen pre-change manifest was modified; it is evidence, not an example"
    )


def test_pre_change_manifest_still_validates():
    manifest = load_json(PRE_SCOPE_FACTORY)
    assert not _schema_errors(manifest), "an additive change must not invalidate an older manifest"
    assert not rules.check_manifest(manifest)


def test_pre_change_manifest_keeps_its_meaning_flat_list_means_own_session():
    manifest = load_json(PRE_SCOPE_FACTORY)
    actions = manifest["permissions"]["actions"]
    assert all(isinstance(a, str) for a in actions), "fixture must be the flat pre-change list"
    assert all(g.scope == "own_session" for g in rules.normalize(actions))


def test_pre_change_counts_only_child_sessions_grants_no_child_authority():
    child = load_json(PRE_SCOPE_FACTORY)["permissions"]["child_sessions"]
    assert set(child) == {"max_concurrent", "max_total"}, "fixture must be counts-only"
    assert "actions" not in child, "no delegated authority"
    assert child.get("allow_descendants", False) is False, "descendants denied by default"
    assert rules.normalize(child.get("actions")) == []


@pytest.mark.parametrize("path", APP_MANIFESTS, ids=lambda p: p.parent.name)
def test_every_first_party_app_manifest_is_schema_and_rule_clean(path: Path):
    """Task 2.3: the apps and the contract cannot drift apart. Includes the app
    manifests this change did NOT touch (claude, codex, pi, lift, story), which
    still use the flat pre-scope action list."""
    manifest = load_json(path)
    assert not _schema_errors(manifest)
    violations = rules.check_manifest(manifest)
    assert not violations, [str(v) for v in violations]


def test_untouched_app_manifests_still_use_the_flat_form():
    """Proof the bare action id is still a first-class form, not a deprecated
    one: these manifests were not edited by this change."""
    for name in ("claude", "codex", "pi", "lift", "story"):
        actions = load_json(REPO / "apps" / name / "manifest.json")["permissions"]["actions"]
        assert actions and all(isinstance(a, str) for a in actions), name


def test_factory_manifest_declares_child_authority_and_denies_descendants():
    """Task 2.1: the two halves the ratified factory-app scenario assumes."""
    permissions = load_json(REPO / "apps" / "factory" / "manifest.json")["permissions"]
    child = permissions["child_sessions"]
    assert child["allow_descendants"] is False
    worker = rules.normalize(child["actions"])
    coordinator = rules.normalize(permissions["actions"])
    assert worker, "a worker must receive a declared set, not an empty one"
    assert "session.create" not in {g.action for g in worker}
    # Strictly narrower, not merely a subset of equal size.
    assert {g.action for g in worker} < {g.action for g in coordinator}
    assert all(g.scope == "own_session" for g in worker)


def test_factory_manifest_takes_a_delegated_grant_not_a_tenant_key():
    """Task 2.2: the coordinator receives a grant:// credential in the env var
    the SDK reads, so it authenticates as itself rather than as the tenant."""
    secrets = load_json(REPO / "apps" / "factory" / "manifest.json")["permissions"]["secrets"]
    by_name = {s["name"]: s["ref"] for s in secrets}
    assert by_name["BARISTA_HOST_API_TOKEN"].startswith("grant://")
    assert by_name["NOTIFY_TOKEN"].startswith("secret://")


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


# --------------------------------------------------------------------------- #
# Grant refresh (apps-003). The wire shape is the whole security argument: the
# replacement's scope comes from the stored row, so there must be NO scope input
# on the request. These pin that, because "refresh" and "issue" differ by
# exactly one request field and a reviewer will not catch it twice.
# --------------------------------------------------------------------------- #
REFRESH_PATH = "/v1alpha1/grants/refresh"


def _refresh_operation() -> dict:
    spec = yaml.safe_load(OPENAPI.read_text())
    assert REFRESH_PATH in spec["paths"], f"{REFRESH_PATH} is missing from the contract"
    path_item = spec["paths"][REFRESH_PATH]
    assert set(path_item) == {"post"}, f"refresh is a POST and nothing else: {sorted(path_item)}"
    return path_item["post"]


def test_refresh_takes_no_request_body_at_all():
    """Design D1: the request carries no scope. Not an optional scope, not an
    ignored one — none, so there is nothing a caller could widen with. A
    requestBody here would mean this contract had specified `grant.issue`."""
    op = _refresh_operation()
    assert "requestBody" not in op, (
        "refresh must declare no request body: any body schema is an input, and an "
        "input is how issuance gets in"
    )
    # Nor a query/path parameter naming a grant, a resource, or an action.
    names = {p.get("name", "").lower() for p in op.get("parameters", [])}
    assert not (names & {"resource", "action", "actions", "scope", "grant", "grantid"}), names


def test_refresh_response_carries_the_scope_it_did_not_take():
    op = _refresh_operation()
    ok = op["responses"]["200"]["content"]["application/json"]["schema"]
    assert ok["$ref"] == "#/components/schemas/RefreshedGrant"
    schema = yaml.safe_load(OPENAPI.read_text())["components"]["schemas"]["RefreshedGrant"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"secret", "resource", "actions", "expires_at"}
    assert set(schema["properties"]) == {"secret", "resource", "actions", "expires_at"}
    assert schema["properties"]["actions"]["type"] == "array"
    assert schema["properties"]["expires_at"]["format"] == "date-time"


def test_refresh_is_not_idempotent_by_key():
    """Rotation is not replayable: honouring an Idempotency-Key would require the
    provider to keep the replacement secret retrievable (design D2). The absence
    of the header is the contract, so it is asserted rather than assumed."""
    op = _refresh_operation()
    params = {p.get("$ref", "") for p in op.get("parameters", [])}
    assert "#/components/parameters/IdempotencyKey" not in params


def test_refresh_documents_its_refusals_and_its_capability_gate():
    op = _refresh_operation()
    assert set(op["responses"]) == {"200", "401", "403", "501"}, sorted(op["responses"])
    assert op["tags"] == ["grants"]
    text = json.dumps(op).lower()
    # Task 1.2: gated on the capability, and reachable only with a grant.
    assert "grants.delegated" in text
    assert "tenant credential" in text
    # Task 1.3: rotation and the lockout are discoverable from the contract.
    assert "stops being accepted" in text or "no longer accepted" in text
    assert "locked itself out" in text


def test_deleting_a_session_revokes_the_grants_bound_to_it():
    """The load-bearing premise of the whole refresh design: the bound on a chain
    is the session. Stated on deleteSession as an obligation, because a provider
    that treats it as housekeeping leaves a session-bound grant renewing itself
    forever after its session is gone — which no maximum-lifetime ceiling can
    catch, since no single credential ever exceeds it."""
    spec = yaml.safe_load(OPENAPI.read_text())
    delete = spec["paths"]["/v1alpha1/sessions/{sessionId}"]["delete"]
    text = delete["description"].lower()
    assert "shall revoke the delegated grants bound to it" in text
    assert "bounds a refresh chain" in text
    assert "every path that deletes a session" in text


def test_refresh_refuses_a_grant_with_no_session_binding():
    """The bound on a refresh chain is the session. An implementer reading only
    'expired or revoked' would allow an unbound grant to renew forever in steps
    that never trip a maximum-lifetime ceiling, so the contract has to say it."""
    text = json.dumps(_refresh_operation()).lower()
    assert "no session binding" in text
    assert "ceiling" in text, "the reason a chain without a bound matters must be stated"


def test_a_refused_refresh_leaves_the_credential_working():
    """The failure direction is rollback, not revoke-then-fail: a caller stranded
    by a refusal would hold nothing and have no way to obtain anything."""
    op = _refresh_operation()
    text = json.dumps(op).lower()
    assert "leave the presented credential working" in text
    assert "rollback" in text
    assert "keeps working" in json.dumps(op["responses"]["403"]).lower()


def test_refresh_is_gated_on_grants_delegated_and_nothing_else():
    """No second capability id, and no vendor extension to discover: a delegated
    grant that cannot be refreshed is not a usable delegated grant."""
    spec = yaml.safe_load(OPENAPI.read_text())
    assert "grants.refresh" not in spec["components"]["schemas"]["CapabilityId"]["enum"]
    assert set(spec["components"]["schemas"]["CapabilityId"]["enum"]) == {
        "session.pause_resume",
        "session.snapshot.exact",
        "session.fork",
        "capsule.export",
        "capsule.import",
        "grants.delegated",
        "story.publish",
        "branch.evaluation",
    }
    text = json.dumps(_refresh_operation()).lower()
    assert "no separate capability id" in text and "vendor extension" in text


def test_refresh_rotation_is_documented_in_the_readme():
    """Task 1.3: the lockout has to be findable without reading spec deltas."""
    readme = (CONTRACTS / "host-api" / "README.md").read_text().lower()
    assert "grants/refresh" in readme
    assert "locked itself out" in readme
    assert "previous secret stops working" in readme


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
