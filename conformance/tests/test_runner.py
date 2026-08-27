"""Self-tests for the conformance suite.

These prove the runner correctly certifies a conformant provider, fails a
faker, refuses to certify an advertised-but-untested profile, and enforces the
standalone (Cloud-absent) guard — all offline against an in-process test double.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from mock_provider import MockProvider  # noqa: E402

from barista_conformance import schemas  # noqa: E402
from barista_conformance.config import DelegatedProbe, ProviderConfig  # noqa: E402
from barista_conformance.report import Status, evaluate_conformance  # noqa: E402
from barista_conformance.runner import run_conformance  # noqa: E402
from barista_conformance.standalone import StandaloneViolation, install_guard  # noqa: E402

DELEGATED = "grants.delegated"
CHILD_AUTHORITY_CASES = {
    "grants.child_authority_manifest_accepted",
    "grants.over_delegating_manifest_refused",
    "grants.worker_cannot_create_descendants",
    "grants.child_receives_only_declared_subset",
    "grants.authority_stops_at_own_children",
}
REFRESH_CASES = {
    "grants.refresh_preserves_exactly_the_presented_scope",
    "grants.refresh_rotates_the_previous_secret",
    "grants.refresh_cannot_widen_authority",
    "grants.refresh_refused_after_revocation",
    "grants.refresh_refused_after_expiry",
    "grants.refresh_refuses_a_credential_with_nothing_to_refresh",
}
DELEGATED_CASES = CHILD_AUTHORITY_CASES | REFRESH_CASES

#: Short enough that the expiry case can wait a grant out instead of skipping,
#: long enough that a case's own steps finish inside one. Shortening the
#: PROVIDER is the point: the alternative is a suite that sleeps for the
#: reference provider's fifteen minutes.
SHORT_GRANT_LIFETIME = 2.0


def _config(**kw) -> ProviderConfig:
    base = dict(endpoint="http://mock.local", provider_name="mock", provider_version="0.0.0")
    base.update(kw)
    return ProviderConfig(**base)


def _child_authority_manifest() -> dict:
    path = (
        schemas._contracts_dir() / "app-manifest" / "v1alpha1" / "examples" / "factory.json"
    )
    return json.loads(path.read_text())


def _delegated_provider(**kw) -> tuple[MockProvider, DelegatedProbe]:
    """A provider that mints delegated grants, plus the credentials an OPERATOR
    would hand the suite. The provisioning helper is a fixture, not a provider
    hook: the suite itself only ever reads config.delegated_probe."""
    provider = MockProvider(
        name="delegator", capabilities=[DELEGATED], child_authority=True, **kw
    )
    probe = DelegatedProbe(**provider.provision_delegated_probe(_child_authority_manifest()))
    return provider, probe


def _self_serving_provider(**kw) -> MockProvider:
    """A provider that hands the suite nothing. Everything the delegation cases
    need is obtained through the published contract: a probe session's grant,
    read from the session's environment and confirmed by refreshing it.

    The grant lifetime is left long unless a test needs the expiry case to run,
    because that case waits a grant out in real time.
    """
    return MockProvider(name="delegator", capabilities=[DELEGATED], child_authority=True, **kw)


def _by_id(report) -> dict:
    return {c.id: c for c in report.cases}


def test_core_only_provider_is_conformant():
    provider = MockProvider(name="core-only")
    report = run_conformance(_config(), transport=provider.transport())
    conformant, violations = evaluate_conformance(report)
    assert conformant, violations
    # Optional profiles are honest skips, not failures.
    summary = report.summary()
    assert summary["failed"] == 0
    assert summary["passed"] >= 8
    assert summary["skipped"] >= 1


def test_pause_resume_provider_certifies_the_profile():
    provider = MockProvider(name="pauser", capabilities=["session.pause_resume"])
    report = run_conformance(_config(), transport=provider.transport())
    conformant, violations = evaluate_conformance(report)
    assert conformant, violations
    pr = report.cases_for("session.pause_resume")
    assert pr and all(c.status is Status.PASSED for c in pr)


def test_faker_is_caught():
    """A provider that does not advertise pause_resume but fakes a 200 success
    must fail core.capability_error_not_faked."""
    provider = MockProvider(name="faker", fake_unadvertised_pause=True)
    report = run_conformance(_config(), transport=provider.transport())
    conformant, violations = evaluate_conformance(report)
    assert not conformant
    failed = [c for c in report.cases if c.status is Status.FAILED]
    assert any(c.id == "core.capability_error_not_faked" for c in failed), [c.id for c in failed]


def test_advertised_but_untested_profile_is_not_certified():
    """Advertising a profile the suite has no cases for cannot pass — a skip
    never satisfies an advertised profile (task 2.5)."""
    provider = MockProvider(name="overclaimer", capabilities=["session.fork"])
    report = run_conformance(_config(), transport=provider.transport())
    conformant, violations = evaluate_conformance(report)
    assert not conformant
    assert any("session.fork" in v and "no conformance cases" in v for v in violations), violations


def test_report_is_machine_readable_and_versioned():
    provider = MockProvider(name="core-only", version="1.2.3")
    report = run_conformance(_config(), transport=provider.transport())
    doc = report.to_dict()
    assert doc["contract_version"] == "v1alpha1"
    assert doc["suite_version"]
    assert doc["provider"] == {"name": "core-only", "version": "1.2.3"}
    assert set(doc["summary"]) == {"passed", "failed", "skipped"}
    assert "conformant" in doc and "violations" in doc
    assert all({"id", "profile", "status"} <= set(c) for c in doc["cases"])


# --------------------------------------------------------------------------- #
# Child-session authority (apps-002). The ratified factory-app scenario — "a
# worker without child-create permission calls session create -> denied, while
# the coordinator's create succeeds" — was an unimplementable sentence. These
# prove the cases that implement it detect the real thing, and refuse to certify
# a provider whose delegation they could not watch happen.
# --------------------------------------------------------------------------- #
def test_delegated_grants_provider_certifies_the_profile_unattended():
    """The apps-003 payoff. No operator supplies anything: the suite obtains a
    coordinator credential the way an app does, lets the provider mint a child
    beneath it, and runs every delegated case — including the three apps-002
    shipped written-and-skipped."""
    provider = _self_serving_provider(grant_lifetime_seconds=SHORT_GRANT_LIFETIME)
    config = _config()
    report = run_conformance(config, transport=provider.transport())
    conformant, violations = evaluate_conformance(report)
    assert conformant, violations
    cases = report.cases_for(DELEGATED)
    assert {c.id for c in cases} == DELEGATED_CASES
    assert all(c.status is Status.PASSED for c in cases), [
        (c.id, c.status.value, c.message) for c in cases
    ]
    # And the report says where the credentials came from, either way.
    assert "acquired through the published contract" in report.environment["delegated_credentials"]
    assert report.environment["delegated_probe"] is False
    # The sacrificial probe sessions are cleaned up, not left behind.
    assert config.acquired is not None and config.acquired.sessions == []


def test_partial_credential_acquisition_is_cleaned_up_when_a_request_raises():
    """The coordinator exists before the child request. An exception at that
    boundary used to lose the function-local cleanup list and leak one new probe
    every time a later case retried acquisition."""
    provider = _self_serving_provider()
    real_handle = provider._handle

    def fail_child_creation(request):
        if request.method == "POST" and request.url.path == "/v1alpha1/sessions":
            body = json.loads(request.content or b"{}")
            if str(body.get("name", "")).startswith("conf-probe-worker-"):
                raise RuntimeError("injected child acquisition failure")
        return real_handle(request)

    provider._handle = fail_child_creation
    config = _config()
    report = run_conformance(config, transport=provider.transport())

    failed = [case for case in report.cases if case.status is Status.FAILED]
    assert any("injected child acquisition failure" in case.message for case in failed)
    assert not [
        session
        for session in provider.sessions.values()
        if str(session.get("name", "")).startswith("conf-probe-")
    ], "a partial delegated probe survived the run"
    assert config.acquired is not None and config.acquired.sessions == []


def test_the_three_apps_002_cases_no_longer_need_an_operator():
    """Named explicitly because it is the thing apps-003 exists to fix: these
    three were fully written and permanently skipped for want of a credential no
    client could obtain."""
    provider = _self_serving_provider()
    report = run_conformance(_config(), transport=provider.transport())
    cases = _by_id(report)
    for cid in (
        "grants.worker_cannot_create_descendants",
        "grants.child_receives_only_declared_subset",
        "grants.authority_stops_at_own_children",
    ):
        assert cases[cid].status is Status.PASSED, (cid, cases[cid].message)


def test_operator_supplied_credentials_still_take_precedence():
    """apps-002's path must not regress: credentials handed over are used as
    given. The expiry case is the one that then cannot run — this provider's
    grants outlive the suite's willingness to wait — and it says so instead of
    passing on the revocation case's evidence."""
    provider, probe = _delegated_provider()  # default 900s lifetime
    report = run_conformance(_config(delegated_probe=probe), transport=provider.transport())
    cases = _by_id(report)
    for cid in DELEGATED_CASES - {"grants.refresh_refused_after_expiry"}:
        assert cases[cid].status is Status.PASSED, (cid, cases[cid].message)
    expiry = cases["grants.refresh_refused_after_expiry"]
    assert expiry.status is Status.SKIPPED
    assert "delegated grants live about" in expiry.message
    assert "BARISTA_CONFORMANCE_EXPIRY_WAIT_SECONDS" in expiry.message
    # A skip never certifies, so the profile is not certified on this provider.
    conformant, violations = evaluate_conformance(report)
    assert not conformant
    assert any("refresh_refused_after_expiry" in v for v in violations), violations
    assert report.environment["delegated_credentials"] == "operator-supplied credentials"
    # The suite may rotate operator credentials, but their sessions are not its
    # resources and never enter the suite-owned cleanup ledger.
    assert probe.coordinator_session_id in provider.sessions
    assert probe.worker_session_id in provider.sessions


def test_worker_that_inherits_the_coordinators_authority_is_caught():
    """The hole this change closes: a provider that mints a child the parent's
    own actions instead of the declared subset. The worker can then create
    sessions and read what it was never given."""
    provider, probe = _delegated_provider(child_inherits_parent_authority=True)
    report = run_conformance(_config(delegated_probe=probe), transport=provider.transport())
    conformant, _ = evaluate_conformance(report)
    assert not conformant
    failed = {c.id for c in report.cases if c.status is Status.FAILED}
    assert "grants.worker_cannot_create_descendants" in failed, failed
    assert "grants.child_receives_only_declared_subset" in failed, failed


def test_created_scope_that_reaches_any_session_is_caught():
    """'created_sessions' is a scope, not a licence over the account."""
    provider, probe = _delegated_provider(ignore_created_scope=True)
    report = run_conformance(_config(delegated_probe=probe), transport=provider.transport())
    conformant, _ = evaluate_conformance(report)
    assert not conformant
    failed = {c.id for c in report.cases if c.status is Status.FAILED}
    assert "grants.authority_stops_at_own_children" in failed, failed


def test_provider_that_ignores_the_subset_rule_at_install_is_caught():
    """A provider advertising delegated grants that accepts a manifest handing
    its children more than it holds. The manifest is schema-valid, so only the
    install-time check stands between it and a running over-privileged worker."""
    provider = MockProvider(name="permissive", capabilities=[DELEGATED], child_authority=False)
    report = run_conformance(_config(), transport=provider.transport())
    conformant, _ = evaluate_conformance(report)
    assert not conformant
    failed = {c.id for c in report.cases if c.status is Status.FAILED}
    assert "grants.over_delegating_manifest_refused" in failed, failed


def test_delegated_cases_without_obtainable_credentials_skip_and_cannot_certify():
    """The honest failure mode, unchanged in spirit from apps-002. This provider
    mints grants correctly but delivers them somewhere a client cannot read, so
    the suite has nothing to hold: the cases that need a second principal skip
    saying why, and a skip on an advertised profile is still a violation."""
    provider = MockProvider(
        name="honest-but-unprovable",
        capabilities=[DELEGATED],
        child_authority=True,
        deliver_grant_into_session=False,
    )
    report = run_conformance(_config(), transport=provider.transport())
    conformant, violations = evaluate_conformance(report)
    assert not conformant
    cases = _by_id(report)
    assert cases["grants.child_authority_manifest_accepted"].status is Status.PASSED
    assert cases["grants.over_delegating_manifest_refused"].status is Status.PASSED
    needs_credentials = CHILD_AUTHORITY_CASES - {
        "grants.child_authority_manifest_accepted",
        "grants.over_delegating_manifest_refused",
    }
    for cid in needs_credentials:
        assert cases[cid].status is Status.SKIPPED, cases[cid]
        assert "resolved nothing into" in cases[cid].message
        assert any(cid in v for v in violations), violations
    assert "resolved nothing into" in report.environment["delegated_credentials"]


def test_advertising_delegated_grants_without_offering_refresh_is_caught():
    """A provider advertising the profile now owes the refresh operation: it is
    what makes a grant survivable and what lets anything confirm one."""
    provider = MockProvider(
        name="no-refresh", capabilities=[DELEGATED], child_authority=True, refresh_supported=False
    )
    report = run_conformance(_config(), transport=provider.transport())
    conformant, _ = evaluate_conformance(report)
    assert not conformant
    cases = _by_id(report)
    failed = {c.id for c in report.cases if c.status is Status.FAILED}
    assert "grants.refresh_refuses_a_credential_with_nothing_to_refresh" in failed, failed
    assert cases["grants.refresh_preserves_exactly_the_presented_scope"].status is Status.SKIPPED


def test_a_refresh_that_keeps_the_old_secret_working_is_caught():
    """Rotation that does not rotate. The leaked-secret window would then be the
    session's whole life, which is the trade design D2 refuses."""
    provider = _self_serving_provider(refresh_keeps_old_secret=True)
    report = run_conformance(_config(), transport=provider.transport())
    conformant, _ = evaluate_conformance(report)
    assert not conformant
    failed = {c.id for c in report.cases if c.status is Status.FAILED}
    assert "grants.refresh_rotates_the_previous_secret" in failed, failed
    message = _by_id(report)["grants.refresh_rotates_the_previous_secret"].message
    assert "extension" in message


def test_a_refresh_of_a_grant_with_no_session_binding_is_caught():
    """The bound on a refresh chain is the session. A permissive implementation
    reads the refusal list as 'expired or revoked' and lets an unbound grant
    renew forever in steps that never trip a maximum-lifetime ceiling."""
    honest = _self_serving_provider()
    unbound = honest.provision_unbound_grant(_child_authority_manifest())
    report = run_conformance(
        _config(unbound_grant=unbound), transport=honest.transport()
    )
    case = _by_id(report)["grants.refresh_refuses_a_credential_with_nothing_to_refresh"]
    assert case.status is Status.PASSED, case.message
    assert "grant.unbound" in case.message

    permissive = _self_serving_provider(refresh_unbound_grants=True)
    token = permissive.provision_unbound_grant(_child_authority_manifest())
    report = run_conformance(
        _config(unbound_grant=token), transport=permissive.transport()
    )
    conformant, _ = evaluate_conformance(report)
    assert not conformant
    failed = _by_id(report)["grants.refresh_refuses_a_credential_with_nothing_to_refresh"]
    assert failed.status is Status.FAILED, failed
    assert "nothing ends that chain" in failed.message


def test_a_refresh_that_reads_scope_from_the_request_is_caught():
    """The one that matters: a provider taking the replacement's scope from the
    request body has implemented `grant.issue` under refresh's name, and a
    caller could widen its own authority."""
    provider = _self_serving_provider(refresh_reads_request_scope=True)
    report = run_conformance(_config(), transport=provider.transport())
    conformant, _ = evaluate_conformance(report)
    assert not conformant
    failed = {c.id for c in report.cases if c.status is Status.FAILED}
    assert "grants.refresh_cannot_widen_authority" in failed, failed
    message = _by_id(report)["grants.refresh_cannot_widen_authority"].message
    assert "issuance" in message or "conferred authority" in message


def test_foreign_session_is_required_to_prove_the_scope_boundary():
    """Without a session the coordinator did not create, 'refused' cannot be
    told apart from 'absent' — so the case skips instead of pretending."""
    provider, probe = _delegated_provider()
    probe.foreign_session_id = None
    report = run_conformance(_config(delegated_probe=probe), transport=provider.transport())
    case = _by_id(report)["grants.authority_stops_at_own_children"]
    assert case.status is Status.SKIPPED
    assert "foreign session" in case.message


def test_a_declared_descendant_permission_is_honoured():
    """app-manifest: 'WHEN a manifest explicitly permits its children to create
    sessions THEN a child's session create is permitted.'

    Asserted against the reference double rather than as a conformance case: it
    needs a *second* delegated probe from a manifest with
    `allow_descendants: true`, and adding a second probe to the suite's config
    to certify one scenario is not worth the surface. What this pins is that the
    field has one implementable meaning — the same `_mint` path a provider takes
    (§4.2/§4.3) — so 'descendants are refused by default' is a decision read out
    of the manifest and not a hard-coded no.
    """
    nested = json.loads(
        (
            schemas._contracts_dir()
            / "app-manifest"
            / "v1alpha1"
            / "examples"
            / "nested-fanout.json"
        ).read_text()
    )
    assert nested["permissions"]["child_sessions"]["allow_descendants"] is True

    permissive = MockProvider(name="nesting", capabilities=[DELEGATED], child_authority=True)
    probe = permissive.provision_delegated_probe(nested)
    child = permissive.principals[probe["worker_token"]]
    assert child.may_create_children, "an explicitly permitted child must be able to create"

    # And the default really is the other way round, from the same code path.
    strict = MockProvider(name="flat", capabilities=[DELEGATED], child_authority=True)
    denied = strict.provision_delegated_probe(_child_authority_manifest())
    assert not strict.principals[denied["worker_token"]].may_create_children


def test_core_only_provider_skips_every_delegated_case():
    provider = MockProvider(name="core-only")
    report = run_conformance(_config(), transport=provider.transport())
    conformant, violations = evaluate_conformance(report)
    assert conformant, violations
    for case in report.cases_for(DELEGATED):
        assert case.status is Status.SKIPPED
        assert "not advertised" in case.message


def test_standalone_guard_blocks_cloud_dns():
    import socket

    install_guard(cloud_hosts=("barista.sh",), proprietary_modules=("barista_cloud",))
    with pytest.raises(StandaloneViolation):
        socket.getaddrinfo("api.barista.sh", 443)


def test_standalone_guard_blocks_proprietary_import():
    install_guard(cloud_hosts=(), proprietary_modules=("definitely_proprietary_pkg",))
    with pytest.raises(StandaloneViolation):
        __import__("definitely_proprietary_pkg")
