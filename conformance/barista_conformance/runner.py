"""Conformance runner: discover capabilities, run cases, produce a report."""

from __future__ import annotations

from typing import Optional

import httpx

from . import cases as cases_module
from .client import HostAPIClient
from .config import ProviderConfig
from .profiles import CORE
from .report import CaseResult, ConformanceReport, Status
from .standalone import assert_no_proprietary_modules, install_guard


def _discover_profiles(client: HostAPIClient) -> tuple[list[str], dict]:
    resp = client.discovery()
    resp.raise_for_status()
    body = resp.json()
    advertised = [CORE] if body.get("core_profile") else []
    advertised += list(body.get("capabilities", []))
    return advertised, body


def run_conformance(
    config: ProviderConfig,
    *,
    transport: Optional[httpx.BaseTransport] = None,
    contract_version: str = "v1alpha1",
    suite_version: str = "0.1.0a1",
) -> ConformanceReport:
    """Run the suite against ``config`` and return a report.

    A ``transport`` may be injected to run against an in-process provider (used
    by the suite's own self-tests); production runs leave it None and use HTTP.
    """
    if config.standalone:
        assert_no_proprietary_modules(config.proprietary_modules)
        install_guard(config.cloud_hosts, config.proprietary_modules)

    report = ConformanceReport(
        contract_version=contract_version,
        suite_version=suite_version,
        provider_name=config.provider_name,
        provider_version=config.provider_version,
        advertised_profiles=[],
        standalone=config.standalone,
        environment={"endpoint": config.endpoint, "standalone": config.standalone},
    )

    with HostAPIClient(
        config.endpoint,
        token=config.resolved_token(),
        transport=transport,
        timeout=config.timeout_seconds,
    ) as client:
        advertised, discovery_body = _discover_profiles(client)
        report.advertised_profiles = advertised
        report.provider_name = discovery_body.get("provider", {}).get("name", config.provider_name)
        report.provider_version = discovery_body.get("provider", {}).get(
            "version", config.provider_version
        )

        for case in cases_module.all_cases():
            # Optional-profile cases only run when the provider advertises them;
            # otherwise they are an honest skip that does not certify anything.
            if case.profile != CORE and case.profile not in advertised:
                report.add(
                    CaseResult(
                        id=case.id,
                        profile=case.profile,
                        status=Status.SKIPPED,
                        message=f"profile '{case.profile}' not advertised",
                    )
                )
                continue
            try:
                result = case.fn(client, config, advertised)
                report.add(
                    result
                    if result is not None
                    else CaseResult(id=case.id, profile=case.profile, status=Status.PASSED)
                )
            except AssertionError as exc:
                report.add(
                    CaseResult(
                        id=case.id, profile=case.profile, status=Status.FAILED, message=str(exc)
                    )
                )
            except Exception as exc:  # noqa: BLE001 - report, don't crash the suite
                report.add(
                    CaseResult(
                        id=case.id,
                        profile=case.profile,
                        status=Status.FAILED,
                        message=f"{type(exc).__name__}: {exc}",
                    )
                )

    return report
