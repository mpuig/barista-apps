"""Provider endpoint and credential configuration.

Configuration selects *which* provider to test and how to authenticate. It is
the only thing that differs between running against a local provider and running
against Barista Cloud — the cases themselves never branch on provider name.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ProviderConfig:
    """How to reach and authenticate to a Host API provider."""

    endpoint: str
    """Base URL, e.g. http://localhost:8088 or https://api.barista.sh."""

    token: Optional[str] = None
    """Bearer credential, if the provider requires one."""

    token_env: Optional[str] = None
    """Name of an env var to read the token from (avoids passing secrets on argv)."""

    provider_name: str = "unknown"
    provider_version: str = "unknown"

    standalone: bool = False
    """Run under the mandatory Cloud-absent harness (blocks Cloud DNS/endpoints,
    fails on proprietary imports or network attempts)."""

    cloud_hosts: tuple[str, ...] = field(
        default=(
            "barista.sh",
            "beta.barista.sh",
            "api.barista.sh",
            "cloud.barista.sh",
        )
    )
    """Host suffixes treated as Barista Cloud for the standalone harness."""

    proprietary_modules: tuple[str, ...] = field(default=("barista_cloud",))
    """Import names that must not be reachable in a standalone run."""

    timeout_seconds: float = 30.0

    def resolved_token(self) -> Optional[str]:
        if self.token:
            return self.token
        if self.token_env:
            return os.environ.get(self.token_env)
        return None

    @classmethod
    def from_env(cls) -> "ProviderConfig":
        endpoint = os.environ.get("BARISTA_HOST_API_ENDPOINT")
        if not endpoint:
            raise ValueError(
                "BARISTA_HOST_API_ENDPOINT is required "
                "(e.g. http://localhost:8088)."
            )
        return cls(
            endpoint=endpoint.rstrip("/"),
            token=os.environ.get("BARISTA_HOST_API_TOKEN"),
            token_env=None,
            provider_name=os.environ.get("BARISTA_PROVIDER_NAME", "unknown"),
            provider_version=os.environ.get("BARISTA_PROVIDER_VERSION", "unknown"),
            standalone=os.environ.get("BARISTA_CONFORMANCE_STANDALONE", "") == "1",
        )
