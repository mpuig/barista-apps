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

from . import adapters, attach, errors, sensitive
from .client import BaristaClient
from .config import Config
from .models import Artifact, Discovery, Event, ExecHandle, Grant, InstalledApp, Operation, Session
from .runs import (
    APP_RUN_ENV,
    APP_RUN_MEDIA_TYPE,
    APP_RUN_RESULT_MEDIA_TYPE,
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
    "errors",
    "sensitive",
    "adapters",
    "attach",
]
