"""Claude Code harness adapter.

Claude Code stores one JSONL transcript per session under
``<home>/projects/<cwd-with-slashes-as-dashes>/<session-id>.jsonl``. Lines carry
a ``sessionId``; an optional ``version`` gates transcript compatibility. Shared
logic lives in the SDK base adapter — note that selection is by mtime, since
Claude's filenames are random session ids (not chronological).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from barista_app_sdk.adapters import DetectResult, JsonlSessionAdapter

ADAPTER_NAME = "sh.barista.adapter.claude"
NATIVE_MEDIA_TYPE = "application/vnd.claude-code.transcript+jsonl"
SUPPORTED_TRANSCRIPT_VERSIONS = (1,)


def _default_home() -> Path:
    return Path(os.environ.get("CLAUDE_HOME", os.path.expanduser("~/.claude")))


def _encode_cwd(workspace: str) -> str:
    return workspace.replace("/", "-")


class ClaudeAdapter(JsonlSessionAdapter):
    name = ADAPTER_NAME
    native_media_type = NATIVE_MEDIA_TYPE

    def __init__(self, home: Optional[Path] = None):
        super().__init__(home if home is not None else _default_home())

    def _candidate_files(self, workspace: str) -> list[Path]:
        project_dir = self.home / "projects" / _encode_cwd(workspace)
        return list(project_dir.glob("*.jsonl")) if project_dir.is_dir() else []

    def _version(self, meta: dict) -> int:
        return int(meta.get("version", 1))

    def _supported(self, version) -> bool:
        return version in SUPPORTED_TRANSCRIPT_VERSIONS

    def _session_id(self, meta: dict) -> Optional[str]:
        return meta.get("sessionId")

    def _continuation_command(self, session_id: Optional[str]) -> list[str]:
        return ["claude", "--resume", session_id] if session_id else ["claude"]

    def _supported_versions(self) -> list[str]:
        return [str(v) for v in SUPPORTED_TRANSCRIPT_VERSIONS]

    def _auth_references(self) -> list[str]:
        return ["secret://anthropic/api-key"]

    def _secret_env_values(self) -> list[str]:
        return list(os.environ.get("ANTHROPIC_API_KEY", "").split())

    def detect(self, workspace: str) -> DetectResult:
        # A file must actually be a Claude transcript (carry sessionId), not just
        # any jsonl that happens to sit in the project dir.
        session_file = self._latest(workspace)
        if not session_file:
            return DetectResult(detected=False, reason="no Claude Code session for this workspace")
        if "sessionId" not in self._meta(session_file):
            return DetectResult(detected=False, reason="not a recognizable Claude Code transcript")
        return super().detect(workspace)
