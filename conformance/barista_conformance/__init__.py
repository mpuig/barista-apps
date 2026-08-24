"""Black-box conformance suite for Barista Host API providers.

The suite talks only the published Host API and validates responses against the
open contract schemas in ``contracts/``. It never uses private provider hooks
and it runs offline against any conformant endpoint — including, mandatorily,
one with Barista Cloud unreachable.
"""

from .config import DelegatedProbe, ProviderConfig
from .report import ConformanceReport, evaluate_conformance
from .runner import run_conformance

SUITE_VERSION = "0.1.0a1"
CONTRACT_VERSION = "v1alpha1"

__all__ = [
    "ProviderConfig",
    "DelegatedProbe",
    "ConformanceReport",
    "evaluate_conformance",
    "run_conformance",
    "SUITE_VERSION",
    "CONTRACT_VERSION",
]
