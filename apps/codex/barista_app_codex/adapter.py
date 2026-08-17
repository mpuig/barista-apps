"""Codex CLI harness adapter.

Codex stores date-nested rollout transcripts under
``<home>/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl``. The first line is a
``{"type":"session_meta","payload":{"id":...,"cwd":...,"cli_version":"X.Y.Z"}}``
record; the ``cli_version`` major gates compatibility. Shared logic lives in the
SDK base adapter.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from barista_app_sdk.adapters import JsonlSessionAdapter

ADAPTER_NAME = "sh.barista.adapter.codex"
NATIVE_MEDIA_TYPE = "application/vnd.codex.rollout+jsonl"
SUPPORTED_ROLLOUT_MAJOR_VERSIONS = (0,)


def _default_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", os.path.expanduser("~/.codex")))


def _major(cli_version) -> Optional[int]:
    try:
        return int(str(cli_version).split(".")[0])
    except (ValueError, IndexError):
        return None


class CodexAdapter(JsonlSessionAdapter):
    name = ADAPTER_NAME
    native_media_type = NATIVE_MEDIA_TYPE

    def __init__(self, home: Optional[Path] = None):
        super().__init__(home if home is not None else _default_home())

    def _candidate_files(self, workspace: str) -> list[Path]:
        sessions = self.home / "sessions"
        if not sessions.is_dir():
            return []
        matches: list[Path] = []
        for f in sessions.rglob("rollout-*.jsonl"):
            try:
                if self._meta(f).get("payload", {}).get("cwd") == workspace:
                    matches.append(f)
            except json.JSONDecodeError:
                continue
        return matches

    def _version(self, meta: dict):
        return meta.get("payload", {}).get("cli_version", "")

    def _supported(self, version) -> bool:
        return _major(version) in SUPPORTED_ROLLOUT_MAJOR_VERSIONS

    def _session_id(self, meta: dict) -> Optional[str]:
        return meta.get("payload", {}).get("id")

    def _continuation_command(self, session_id: Optional[str]) -> list[str]:
        return ["codex", "resume", session_id] if session_id else ["codex"]

    def _supported_versions(self) -> list[str]:
        return [f"{m}.x" for m in SUPPORTED_ROLLOUT_MAJOR_VERSIONS]

    def _auth_references(self) -> list[str]:
        return ["secret://openai/api-key"]

    def _secret_env_values(self) -> list[str]:
        return list(os.environ.get("OPENAI_API_KEY", "").split())
