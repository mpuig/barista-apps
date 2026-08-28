"""Non-secret and secret configuration boundaries for the webhook controller."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")


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
    triage_app: str = "github-issue-triage"
    worker_app: str = "github-issue-worker"
    base_ref: str = "main"
    database: Path = Path("github-factory-demo.sqlite3")
    result_directory: Path = Path("github-factory-results")
    max_webhook_bytes: int = 1024 * 1024
    max_patch_bytes: int = 16 * 1024 * 1024
    concurrency: int = 2
    authorized_responders: tuple[str, ...] = ()
    controller_login: str | None = None

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
        logins = (
            *self.authorized_responders,
            *((self.controller_login,) if self.controller_login else ()),
        )
        if any(_LOGIN.fullmatch(login) is None for login in logins):
            raise ValueError("GitHub responder logins are invalid")

    @property
    def full_name(self) -> str:
        return self.repository.removeprefix("https://github.com/")

    @property
    def responders(self) -> frozenset[str]:
        configured = self.authorized_responders or (self.full_name.split("/", 1)[0],)
        return frozenset(login.casefold() for login in configured)

    @classmethod
    def from_env(cls) -> ControllerConfig:
        repository = _required("BARISTA_GITHUB_REPOSITORY")
        responders = tuple(
            item.strip()
            for item in os.environ.get(
                "BARISTA_GITHUB_AUTHORIZED_RESPONDERS", ""
            ).split(",")
            if item.strip()
        )
        return cls(
            repository=repository,
            webhook_secret=_required("BARISTA_GITHUB_WEBHOOK_SECRET"),
            github_token=_required("BARISTA_GITHUB_TOKEN"),
            factory_app=os.environ.get("BARISTA_FACTORY_APP", "factory@0.1.0"),
            triage_app=os.environ.get(
                "BARISTA_FACTORY_TRIAGE_APP", "github-issue-triage"
            ),
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
            authorized_responders=responders,
            controller_login=os.environ.get("BARISTA_GITHUB_CONTROLLER_LOGIN") or None,
        )

    def public_document(self) -> dict:
        return {
            "repository": self.repository,
            "factory_app": self.factory_app,
            "triage_app": self.triage_app,
            "worker_app": self.worker_app,
            "base_ref": self.base_ref,
            "database": str(self.database),
            "result_directory": str(self.result_directory),
            "max_webhook_bytes": self.max_webhook_bytes,
            "max_patch_bytes": self.max_patch_bytes,
            "concurrency": self.concurrency,
            "authorized_responders": sorted(self.responders),
            "controller_login": self.controller_login,
        }


DEFAULT_TRIAGE_COMMAND = ["/usr/local/bin/barista-demo-issue-triage"]
DEFAULT_WORKER_COMMAND = ["/usr/local/bin/barista-demo-issue-worker"]


def triage_command_from_env() -> list[str]:
    return _command_from_env("BARISTA_FACTORY_TRIAGE_COMMAND", DEFAULT_TRIAGE_COMMAND)


def worker_command_from_env() -> list[str]:
    return _command_from_env("BARISTA_FACTORY_WORKER_COMMAND", DEFAULT_WORKER_COMMAND)


def _command_from_env(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name)
    if not raw:
        return list(default)
    value = json.loads(raw)
    if (
        not isinstance(value, list)
        or not value
        or len(value) > 128
        or any(
            not isinstance(item, str) or not item or len(item) > 8192 for item in value
        )
    ):
        raise ValueError(f"{name} must be a bounded JSON argv array")
    return value
