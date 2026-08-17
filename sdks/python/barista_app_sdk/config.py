"""Configuration-only provider selection.

An app targets the Host API and picks *which* provider through configuration
alone — an endpoint plus a credential source. App business logic never branches
on ``local`` versus ``cloud``; it branches only on discovered capabilities.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class Config:
    endpoint: str
    """Host API base URL: a local socket adapter URL, Barista Cloud, or a third party."""

    token: Optional[str] = None
    token_env: Optional[str] = None
    """Read the bearer token from this env var (keeps secrets off argv/config files)."""

    timeout_seconds: float = 30.0

    def resolved_token(self) -> Optional[str]:
        if self.token:
            return self.token
        if self.token_env:
            return os.environ.get(self.token_env)
        return None

    @classmethod
    def from_env(cls) -> "Config":
        endpoint = os.environ.get("BARISTA_HOST_API_ENDPOINT")
        if not endpoint:
            raise ValueError("BARISTA_HOST_API_ENDPOINT is required")
        return cls(
            endpoint=endpoint.rstrip("/"),
            token=os.environ.get("BARISTA_HOST_API_TOKEN"),
            token_env=None,
        )
