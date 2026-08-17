"""Versioned, machine-readable conformance report and its evaluation rule.

A report records the contract version, suite version, provider identity,
advertised profiles, per-case results, and environment constraints. The central
honesty rule (task 2.5): **a skip never counts as passing an advertised
requirement.** A provider is conformant only when the core profile fully passes
and every advertised optional profile has at least one case and all of its
cases passed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional

from .profiles import CORE


class Status(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class CaseResult:
    id: str
    profile: str
    status: Status
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d


@dataclass
class ConformanceReport:
    contract_version: str
    suite_version: str
    provider_name: str
    provider_version: str
    advertised_profiles: list[str]
    standalone: bool
    environment: dict[str, Any] = field(default_factory=dict)
    cases: list[CaseResult] = field(default_factory=list)

    def add(self, result: CaseResult) -> None:
        self.cases.append(result)

    # -- summaries -------------------------------------------------------- #
    def summary(self) -> dict[str, int]:
        counts = {s.value: 0 for s in Status}
        for c in self.cases:
            counts[c.status.value] += 1
        return counts

    def cases_for(self, profile: str) -> list[CaseResult]:
        return [c for c in self.cases if c.profile == profile]

    # -- serialization ---------------------------------------------------- #
    def to_dict(self) -> dict[str, Any]:
        conformant, violations = evaluate_conformance(self)
        return {
            "contract_version": self.contract_version,
            "suite_version": self.suite_version,
            "provider": {"name": self.provider_name, "version": self.provider_version},
            "advertised_profiles": sorted(self.advertised_profiles),
            "standalone": self.standalone,
            "environment": self.environment,
            "summary": self.summary(),
            "conformant": conformant,
            "violations": violations,
            "cases": [c.to_dict() for c in self.cases],
        }


def evaluate_conformance(report: ConformanceReport) -> tuple[bool, list[str]]:
    """Return (conformant, violations).

    Rules:
      * The core profile must have at least one case and no failures/skips.
      * Every advertised optional profile must have at least one case and all
        of its cases must have passed (a skip or failure is a violation —
        advertising a profile you cannot demonstrate is dishonest).
      * A non-advertised optional profile may be skipped freely.
    """
    violations: list[str] = []

    core_cases = report.cases_for(CORE)
    if not core_cases:
        violations.append("core: no cases were run")
    for c in core_cases:
        if c.status is not Status.PASSED:
            violations.append(f"core: case '{c.id}' is {c.status.value}: {c.message}")

    for profile in report.advertised_profiles:
        if profile == CORE:
            continue
        pcases = report.cases_for(profile)
        if not pcases:
            violations.append(
                f"{profile}: advertised but has no conformance cases (skip cannot satisfy it)"
            )
            continue
        for c in pcases:
            if c.status is not Status.PASSED:
                violations.append(
                    f"{profile}: advertised but case '{c.id}' is {c.status.value}: {c.message}"
                )

    return (len(violations) == 0, violations)
