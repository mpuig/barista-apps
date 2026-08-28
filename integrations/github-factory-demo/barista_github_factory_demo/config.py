"""Non-secret and secret configuration boundaries for the webhook controller."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


def _required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise ValueError(f"{name} is required")
    return value


@dataclass(frozen=True)
class ControllerConfig:
    repository: str
    webhook_secret: str
    github_token: str
    factory_app: str = "factory@0.1.0"
    worker_app: str = "github-issue-worker"
    base_ref: str = "main"
    database: Path = Path("github-factory-demo.sqlite3")
    result_directory: Path = Path("github-factory-results")
    max_webhook_bytes: int = 1024 * 1024
    max_patch_bytes: int = 16 * 1024 * 1024
    concurrency: int = 2

    def __post_init__(self) -> None:
        parsed = urlparse(self.repository)
        parts = [part for part in parsed.path.split("/") if part]
        canonical = f"https://github.com/{'/'.join(parts)}"
        if (
            parsed.scheme != "https"
            or parsed.hostname != "github.com"
            or len(parts) != 2
            or self.repository != canonical
        ):
            raise ValueError(
                "repository must be canonical https://github.com/OWNER/REPO"
            )
        if parts[1].endswith(".git"):
            raise ValueError("repository must omit the .git suffix")
        if not self.webhook_secret or not self.github_token:
            raise ValueError("webhook secret and GitHub token must be non-empty")
        if not (1 <= self.max_webhook_bytes <= 4 * 1024 * 1024):
            raise ValueError("max_webhook_bytes is outside the supported bound")
        if not (1 <= self.max_patch_bytes <= 16 * 1024 * 1024):
            raise ValueError("max_patch_bytes is outside the supported bound")
        if not (1 <= self.concurrency <= 16):
            raise ValueError("concurrency must be between 1 and 16")

    @property
    def full_name(self) -> str:
        return self.repository.removeprefix("https://github.com/")

    @classmethod
    def from_env(cls) -> ControllerConfig:
        return cls(
            repository=_required("BARISTA_GITHUB_REPOSITORY"),
            webhook_secret=_required("BARISTA_GITHUB_WEBHOOK_SECRET"),
            github_token=_required("BARISTA_GITHUB_TOKEN"),
            factory_app=os.environ.get("BARISTA_FACTORY_APP", "factory@0.1.0"),
            worker_app=os.environ.get(
                "BARISTA_FACTORY_WORKER_APP", "github-issue-worker"
            ),
            base_ref=os.environ.get("BARISTA_GITHUB_BASE_REF", "main"),
            database=Path(
                os.environ.get("BARISTA_GITHUB_DEMO_DB", "github-factory-demo.sqlite3")
            ),
            result_directory=Path(
                os.environ.get("BARISTA_GITHUB_DEMO_RESULTS", "github-factory-results")
            ),
            max_webhook_bytes=int(
                os.environ.get("BARISTA_GITHUB_MAX_WEBHOOK_BYTES", "1048576")
            ),
            max_patch_bytes=int(
                os.environ.get("BARISTA_GITHUB_MAX_PATCH_BYTES", "16777216")
            ),
            concurrency=int(os.environ.get("BARISTA_GITHUB_CONCURRENCY", "2")),
        )

    def public_document(self) -> dict:
        return {
            "repository": self.repository,
            "factory_app": self.factory_app,
            "worker_app": self.worker_app,
            "base_ref": self.base_ref,
            "database": str(self.database),
            "result_directory": str(self.result_directory),
            "max_webhook_bytes": self.max_webhook_bytes,
            "max_patch_bytes": self.max_patch_bytes,
            "concurrency": self.concurrency,
        }


DEFAULT_WORKER_COMMAND = ["/usr/local/bin/barista-demo-issue-worker"]


def worker_command_from_env() -> list[str]:
    raw = os.environ.get("BARISTA_FACTORY_WORKER_COMMAND")
    if not raw:
        return list(DEFAULT_WORKER_COMMAND)
    value = json.loads(raw)
    if (
        not isinstance(value, list)
        or not value
        or len(value) > 128
        or any(
            not isinstance(item, str) or not item or len(item) > 8192 for item in value
        )
    ):
        raise ValueError(
            "BARISTA_FACTORY_WORKER_COMMAND must be a bounded JSON argv array"
        )
    return value
