"""Self-tests for the conformance suite.

These prove the runner correctly certifies a conformant provider, fails a
faker, refuses to certify an advertised-but-untested profile, and enforces the
standalone (Cloud-absent) guard — all offline against an in-process test double.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from mock_provider import MockProvider  # noqa: E402

from barista_conformance.config import ProviderConfig  # noqa: E402
from barista_conformance.report import Status, evaluate_conformance  # noqa: E402
from barista_conformance.runner import run_conformance  # noqa: E402
from barista_conformance.standalone import StandaloneViolation, install_guard  # noqa: E402


def _config(**kw) -> ProviderConfig:
    base = dict(endpoint="http://mock.local", provider_name="mock", provider_version="0.0.0")
    base.update(kw)
    return ProviderConfig(**base)


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


def test_standalone_guard_blocks_cloud_dns():
    import socket

    install_guard(cloud_hosts=("barista.sh",), proprietary_modules=("barista_cloud",))
    with pytest.raises(StandaloneViolation):
        socket.getaddrinfo("api.barista.sh", 443)


def test_standalone_guard_blocks_proprietary_import():
    install_guard(cloud_hosts=(), proprietary_modules=("definitely_proprietary_pkg",))
    with pytest.raises(StandaloneViolation):
        __import__("definitely_proprietary_pkg")
