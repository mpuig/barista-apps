"""Pi harness adapter.

Pi stores one JSONL session per workspace under
``<home>/sessions/--<cwd-with-slashes-as-dashes>--/<timestamp>_<uuid>.jsonl``.
The first line is a ``{"type":"session","version":N,"id":...}`` record. All the
shared logic (newest-by-mtime selection, opaque export, fidelity, loud version
refusal, continuation) lives in the SDK base adapter; this declares only what is
Pi-specific.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from barista_app_sdk.adapters import JsonlSessionAdapter

ADAPTER_NAME = "sh.barista.adapter.pi"
NATIVE_MEDIA_TYPE = "application/vnd.pi.session+jsonl"
SUPPORTED_SESSION_VERSIONS = (3,)


def _default_home() -> Path:
    return Path(os.environ.get("PI_HOME", os.path.expanduser("~/.pi/agent")))


def _encode_cwd(workspace: str) -> str:
    return "--" + workspace.strip("/").replace("/", "-") + "--"


class PiAdapter(JsonlSessionAdapter):
    name = ADAPTER_NAME
    native_media_type = NATIVE_MEDIA_TYPE

    def __init__(self, home: Optional[Path] = None):
        super().__init__(home if home is not None else _default_home())

    def _candidate_files(self, workspace: str) -> list[Path]:
        session_dir = self.home / "sessions" / _encode_cwd(workspace)
        return list(session_dir.glob("*.jsonl")) if session_dir.is_dir() else []

    def _version(self, meta: dict):
        return meta.get("version")

    def _supported(self, version) -> bool:
        return version in SUPPORTED_SESSION_VERSIONS

    def _session_id(self, meta: dict) -> Optional[str]:
        return meta.get("id")

    def _continuation_command(self, session_id: Optional[str]) -> list[str]:
        return ["pi", "--resume", session_id] if session_id else ["pi"]

    def _supported_versions(self) -> list[str]:
        return [str(v) for v in SUPPORTED_SESSION_VERSIONS]

    def _auth_references(self) -> list[str]:
        return ["secret://model-provider/api-key"]

    def _secret_env_values(self) -> list[str]:
        return list(os.environ.get("MODEL_API_KEY", "").split())
