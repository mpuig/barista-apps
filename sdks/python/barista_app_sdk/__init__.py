"""Barista App SDK — a provider-neutral client for the open Host API.

    from barista_app_sdk import BaristaClient, Config

    with BaristaClient(Config(endpoint="http://localhost:8088")) as client:
        client.negotiate(required=["session.pause_resume"])
        app = client.install_app(manifest)
        session = client.ensure_session(app["name"], name="work")
        handle = client.exec(session.id, ["echo", "hi"])
        client.wait_operation(handle.operation_id)

The same code runs against Barista Cloud by changing only the endpoint/token.
"""

from __future__ import annotations

from . import adapters, attach, errors, forge, sensitive, sources
from .client import BaristaClient
from .config import Config
from .lifecycle import (
    APP_RUN_RESULT_ARTIFACT,
    APP_RUN_RESULT_PATH,
    CollectedAppRun,
    collect_app_run_result,
    hold_app_run_result,
    register_app_run_result,
    wait_app_run,
)
from .models import Artifact, Discovery, Event, ExecHandle, Grant, InstalledApp, Operation, Session
from .forge import (
    DRAFT_PULL_REQUEST_KIND,
    GITHUB_ISSUE_KIND,
    BranchOutput,
    DraftChange,
    FakeForge,
    ForgeAdapter,
    ForgeIssue,
    GitHubForge,
    PatchArtifact,
    commit_workspace_branch,
    create_workspace_patch,
    deliver_draft_change,
    resolve_issue_objective,
)
from .resolution import (
    ResolvedApp,
    resolve_app,
    resolve_installed_app,
    resolve_local_app,
    resolve_remote_app,
)
from .sources import (
    GIT_REPOSITORY_KIND,
    ResolvedGitRepository,
    ResolvedObjective,
    materialize_git_repository,
    resolve_git_commit,
    resolve_local_objective,
)
from .runs import (
    APP_RUN_ENV,
    APP_RUN_MEDIA_TYPE,
    APP_RUN_RESULT_MEDIA_TYPE,
    APP_SESSION_ID_ENV,
    AppRun,
    AppRunResult,
    DeliveryRequest,
    RunBinding,
    RunOperation,
    SecretReference,
    RunSlot,
    canonical_bytes,
    content_id,
    validate_run,
)

__version__ = "0.1.0a1"

__all__ = [
    "BaristaClient",
    "Config",
    "Discovery",
    "Session",
    "Operation",
    "ExecHandle",
    "Artifact",
    "Event",
    "Grant",
    "InstalledApp",
    "ResolvedApp",
    "resolve_app",
    "resolve_installed_app",
    "resolve_local_app",
    "resolve_remote_app",
    "AppRun",
    "AppRunResult",
    "RunBinding",
    "DeliveryRequest",
    "RunOperation",
    "RunSlot",
    "SecretReference",
    "validate_run",
    "canonical_bytes",
    "content_id",
    "APP_RUN_ENV",
    "APP_RUN_MEDIA_TYPE",
    "APP_RUN_RESULT_MEDIA_TYPE",
    "APP_SESSION_ID_ENV",
    "APP_RUN_RESULT_ARTIFACT",
    "APP_RUN_RESULT_PATH",
    "GIT_REPOSITORY_KIND",
    "GITHUB_ISSUE_KIND",
    "DRAFT_PULL_REQUEST_KIND",
    "ResolvedGitRepository",
    "ResolvedObjective",
    "ForgeIssue",
    "DraftChange",
    "BranchOutput",
    "PatchArtifact",
    "FakeForge",
    "ForgeAdapter",
    "GitHubForge",
    "resolve_git_commit",
    "materialize_git_repository",
    "resolve_local_objective",
    "resolve_issue_objective",
    "create_workspace_patch",
    "commit_workspace_branch",
    "deliver_draft_change",
    "CollectedAppRun",
    "collect_app_run_result",
    "register_app_run_result",
    "hold_app_run_result",
    "wait_app_run",
    "errors",
    "sensitive",
    "adapters",
    "attach",
    "sources",
    "forge",
]
