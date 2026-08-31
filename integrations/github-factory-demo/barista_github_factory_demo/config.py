"""Non-secret and secret configuration boundaries for the webhook controller."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
_PROJECT_STATES = (
    "accepted",
    "running",
    "awaiting_input",
    "refused",
    "succeeded",
    "failed",
)
DEFAULT_PROJECT_STATUS_OPTIONS = (
    ("accepted", "Todo"),
    ("running", "In Progress"),
    ("awaiting_input", "Todo"),
    ("refused", "Done"),
    ("succeeded", "Done"),
    ("failed", "Done"),
)


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
    brd_author_app: str = "github-brd-author"
    planner_app: str = "github-feature-planner"
    feature_worker_app: str = "github-feature-worker"
    base_ref: str = "main"
    database: Path = Path("github-factory-demo.sqlite3")
    result_directory: Path = Path("github-factory-results")
    max_webhook_bytes: int = 1024 * 1024
    max_patch_bytes: int = 16 * 1024 * 1024
    concurrency: int = 2
    authorized_responders: tuple[str, ...] = ()
    controller_login: str | None = None
    github_project_token: str | None = None
    github_project_number: int | None = None
    github_project_owner: str | None = None
    github_project_owner_kind: str = "user"
    github_project_status_field: str = "Status"
    github_project_status_options: tuple[tuple[str, str], ...] = (
        DEFAULT_PROJECT_STATUS_OPTIONS
    )
    activity_endpoint: str | None = None
    activity_token: str | None = None
    activity_source_url: str | None = None
    host_api_token: str | None = None
    activity_deploy_command: tuple[str, ...] = ()
    activity_deploy_timeout_seconds: int = 1800
    presenter_token: str | None = None
    presenter_public_url: str | None = None

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
        projection_values = (self.github_project_token, self.github_project_number)
        if any(value is not None for value in projection_values) and not all(
            value is not None for value in projection_values
        ):
            raise ValueError(
                "project token and project number must be configured together"
            )
        if (
            self.github_project_token is not None
            and self.github_project_token == self.github_token
        ):
            raise ValueError(
                "forge and project authority must use separate credentials"
            )
        activity_values = (self.activity_endpoint, self.activity_token)
        if any(value is not None for value in activity_values) and not all(
            value is not None for value in activity_values
        ):
            raise ValueError("activity endpoint and token must be configured together")
        for name, url in (
            ("activity endpoint", self.activity_endpoint),
            ("activity source URL", self.activity_source_url),
        ):
            if url is None:
                continue
            parsed_url = urlparse(url)
            canonical_url = f"https://{parsed_url.netloc}{parsed_url.path.rstrip('/')}"
            if (
                parsed_url.scheme != "https"
                or not parsed_url.hostname
                or parsed_url.username
                or parsed_url.password
                or parsed_url.query
                or parsed_url.fragment
                or url != canonical_url
            ):
                raise ValueError(f"{name} must be canonical credential-free HTTPS")
        if self.activity_token is not None and self.activity_token in {
            self.github_token,
            self.github_project_token,
            self.host_api_token,
        }:
            raise ValueError(
                "activity, forge, project, and Host API authorities must use separate credentials"
            )
        if self.presenter_token is not None:
            if len(self.presenter_token.encode("utf-8")) < 32:
                raise ValueError("presenter token must contain at least 32 bytes")
            if self.presenter_token in {
                self.webhook_secret,
                self.github_token,
                self.github_project_token,
                self.activity_token,
                self.host_api_token,
            }:
                raise ValueError("presenter authority must use a separate credential")
        if self.presenter_public_url is not None:
            presenter_url = urlparse(self.presenter_public_url)
            if (
                presenter_url.scheme != "https"
                or not presenter_url.hostname
                or presenter_url.username
                or presenter_url.password
                or presenter_url.path
                or presenter_url.query
                or presenter_url.fragment
                or self.presenter_public_url != f"https://{presenter_url.netloc}"
            ):
                raise ValueError(
                    "presenter public URL must be canonical credential-free HTTPS"
                )
        if (
            self.activity_deploy_command
            and not Path(self.activity_deploy_command[0]).is_absolute()
        ):
            raise ValueError(
                "activity deploy command must use an absolute executable path"
            )
        if any(not item or len(item) > 8192 for item in self.activity_deploy_command):
            raise ValueError("activity deploy command is invalid")
        if not (30 <= self.activity_deploy_timeout_seconds <= 3600):
            raise ValueError("activity deploy timeout is outside the supported bound")
        if self.github_project_number is not None and not (
            1 <= self.github_project_number <= 10000
        ):
            raise ValueError("GitHub project number is outside the supported bound")
        owner = self.github_project_owner or parts[0]
        if _LOGIN.fullmatch(owner) is None:
            raise ValueError("GitHub project owner is invalid")
        if self.github_project_owner_kind not in {"user", "organization"}:
            raise ValueError("GitHub project owner kind is invalid")
        if (
            not self.github_project_status_field
            or len(self.github_project_status_field) > 64
        ):
            raise ValueError("GitHub project status field is invalid")
        options = dict(self.github_project_status_options)
        if (
            len(options) != len(_PROJECT_STATES)
            or set(options) != set(_PROJECT_STATES)
            or any(not value or len(value) > 64 for value in options.values())
        ):
            raise ValueError(
                "GitHub project status options must map every controller state"
            )

    @property
    def full_name(self) -> str:
        return self.repository.removeprefix("https://github.com/")

    @property
    def responders(self) -> frozenset[str]:
        configured = self.authorized_responders or (self.full_name.split("/", 1)[0],)
        return frozenset(login.casefold() for login in configured)

    @property
    def project_enabled(self) -> bool:
        return self.github_project_number is not None

    @property
    def activity_enabled(self) -> bool:
        return self.activity_endpoint is not None

    @property
    def activity_deploy_enabled(self) -> bool:
        return bool(self.activity_deploy_command)

    @property
    def project_owner(self) -> str:
        return self.github_project_owner or self.full_name.split("/", 1)[0]

    @property
    def project_status_options(self) -> dict[str, str]:
        return dict(self.github_project_status_options)

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
        project_options_raw = os.environ.get("BARISTA_GITHUB_PROJECT_STATUS_OPTIONS")
        project_options = DEFAULT_PROJECT_STATUS_OPTIONS
        if project_options_raw:
            parsed = json.loads(project_options_raw)
            if not isinstance(parsed, dict) or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in parsed.items()
            ):
                raise ValueError(
                    "BARISTA_GITHUB_PROJECT_STATUS_OPTIONS must be a string map"
                )
            project_options = tuple(parsed.items())
        project_number_raw = os.environ.get("BARISTA_GITHUB_PROJECT_NUMBER")
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
            brd_author_app=os.environ.get(
                "BARISTA_FACTORY_BRD_AUTHOR_APP", "github-brd-author"
            ),
            planner_app=os.environ.get(
                "BARISTA_FACTORY_PLANNER_APP", "github-feature-planner"
            ),
            feature_worker_app=os.environ.get(
                "BARISTA_FACTORY_FEATURE_WORKER_APP", "github-feature-worker"
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
            github_project_token=os.environ.get("BARISTA_GITHUB_PROJECT_TOKEN") or None,
            github_project_number=(
                int(project_number_raw) if project_number_raw else None
            ),
            github_project_owner=os.environ.get("BARISTA_GITHUB_PROJECT_OWNER") or None,
            github_project_owner_kind=os.environ.get(
                "BARISTA_GITHUB_PROJECT_OWNER_KIND", "user"
            ),
            github_project_status_field=os.environ.get(
                "BARISTA_GITHUB_PROJECT_STATUS_FIELD", "Status"
            ),
            github_project_status_options=project_options,
            activity_endpoint=os.environ.get("BARISTA_ACTIVITY_ENDPOINT") or None,
            activity_token=os.environ.get("BARISTA_ACTIVITY_TOKEN") or None,
            activity_source_url=os.environ.get("BARISTA_ACTIVITY_SOURCE_URL") or None,
            host_api_token=os.environ.get("BARISTA_HOST_API_TOKEN") or None,
            activity_deploy_command=tuple(
                _optional_command_from_env("BARISTA_ACTIVITY_DEPLOY_COMMAND")
            ),
            activity_deploy_timeout_seconds=int(
                os.environ.get("BARISTA_ACTIVITY_DEPLOY_TIMEOUT_SECONDS", "1800")
            ),
            presenter_token=os.environ.get("BARISTA_DEMO_PRESENTER_TOKEN") or None,
            presenter_public_url=os.environ.get("BARISTA_DEMO_PUBLIC_URL") or None,
        )

    def public_document(self) -> dict:
        return {
            "repository": self.repository,
            "factory_app": self.factory_app,
            "triage_app": self.triage_app,
            "worker_app": self.worker_app,
            "brd_author_app": self.brd_author_app,
            "planner_app": self.planner_app,
            "feature_worker_app": self.feature_worker_app,
            "base_ref": self.base_ref,
            "database": str(self.database),
            "result_directory": str(self.result_directory),
            "max_webhook_bytes": self.max_webhook_bytes,
            "max_patch_bytes": self.max_patch_bytes,
            "concurrency": self.concurrency,
            "authorized_responders": sorted(self.responders),
            "controller_login": self.controller_login,
            "activity": {
                "enabled": self.activity_enabled,
                "endpoint": self.activity_endpoint,
                "source_url": self.activity_source_url,
                "deploy_enabled": self.activity_deploy_enabled,
            },
            "presenter": {
                "enabled": self.presenter_token is not None,
                "url": (
                    f"{self.presenter_public_url}/presenter"
                    if self.presenter_public_url
                    else None
                ),
            },
            "project": {
                "enabled": self.project_enabled,
                "owner": self.project_owner if self.project_enabled else None,
                "owner_kind": self.github_project_owner_kind
                if self.project_enabled
                else None,
                "number": self.github_project_number,
                "status_field": self.github_project_status_field,
                "status_options": self.project_status_options,
            },
        }


DEFAULT_TRIAGE_COMMAND = ["/usr/local/bin/barista-demo-issue-triage"]
DEFAULT_WORKER_COMMAND = ["/usr/local/bin/barista-demo-issue-worker"]
DEFAULT_BRD_AUTHOR_COMMAND = ["/usr/local/bin/barista-demo-brd-author"]
DEFAULT_PLANNER_COMMAND = ["/usr/local/bin/barista-demo-feature-planner"]
DEFAULT_FEATURE_WORKER_COMMAND = ["/usr/local/bin/barista-demo-feature-worker"]


def triage_command_from_env() -> list[str]:
    return _command_from_env("BARISTA_FACTORY_TRIAGE_COMMAND", DEFAULT_TRIAGE_COMMAND)


def worker_command_from_env() -> list[str]:
    return _command_from_env("BARISTA_FACTORY_WORKER_COMMAND", DEFAULT_WORKER_COMMAND)


def brd_author_command_from_env() -> list[str]:
    return _command_from_env(
        "BARISTA_FACTORY_BRD_AUTHOR_COMMAND", DEFAULT_BRD_AUTHOR_COMMAND
    )


def planner_command_from_env() -> list[str]:
    return _command_from_env("BARISTA_FACTORY_PLANNER_COMMAND", DEFAULT_PLANNER_COMMAND)


def feature_worker_command_from_env() -> list[str]:
    return _command_from_env(
        "BARISTA_FACTORY_FEATURE_WORKER_COMMAND", DEFAULT_FEATURE_WORKER_COMMAND
    )


def _optional_command_from_env(name: str) -> list[str]:
    raw = os.environ.get(name)
    if not raw:
        return []
    return _parse_command(name, raw)


def _command_from_env(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name)
    if not raw:
        return list(default)
    return _parse_command(name, raw)


def _parse_command(name: str, raw: str) -> list[str]:
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
