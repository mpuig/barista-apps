"""Claude Code harness adapter.

Detects Claude Code native session state, exports it as an opaque semantic
bundle with an honest fidelity report, and builds a continuation launch. The
native transcript bytes are preserved verbatim; no provider-specific fields are
introduced.

Claude Code stores one JSONL transcript per session under
``<home>/projects/<cwd-with-slashes-as-dashes>/<session-id>.jsonl``. Lines carry
a ``sessionId``; an optional ``version`` gates transcript compatibility.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Optional

from barista_app_sdk.adapters import (
    AdapterCapabilities,
    AdapterCompatibilityError,
    AdapterResult,
    Attachment,
    DetectResult,
    FidelityReport,
    LaunchSpec,
    SemanticBundle,
)
from barista_app_sdk.sensitive import assert_no_secret_values

ADAPTER_NAME = "sh.barista.adapter.claude"
NATIVE_MEDIA_TYPE = "application/vnd.claude-code.transcript+jsonl"
SUPPORTED_TRANSCRIPT_VERSIONS = (1,)


def _default_home() -> Path:
    return Path(os.environ.get("CLAUDE_HOME", os.path.expanduser("~/.claude")))


def _encode_cwd(workspace: str) -> str:
    return workspace.replace("/", "-")


class ClaudeAdapter:
    name = ADAPTER_NAME

    def __init__(self, home: Optional[Path] = None):
        self.home = Path(home) if home else _default_home()

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            name=self.name,
            supported_versions=[str(v) for v in SUPPORTED_TRANSCRIPT_VERSIONS],
            semantic_export=True,
            continuation=True,
            native_media_types=[NATIVE_MEDIA_TYPE],
            auth_references=["secret://anthropic/api-key"],
            limitations=[
                "Semantic continuation replays a transcript; it is not exact process-memory transfer.",
            ],
        )

    def _session_file(self, workspace: str) -> Optional[Path]:
        project_dir = self.home / "projects" / _encode_cwd(workspace)
        if not project_dir.is_dir():
            return None
        files = sorted(f for f in project_dir.glob("*.jsonl"))
        return files[-1] if files else None

    def _meta(self, session_file: Path) -> dict:
        with session_file.open() as fh:
            return json.loads(fh.readline())

    def _version(self, meta: dict) -> int:
        return int(meta.get("version", 1))

    def detect(self, workspace: str) -> DetectResult:
        session_file = self._session_file(workspace)
        if not session_file:
            return DetectResult(detected=False, reason="no Claude Code session for this workspace")
        meta = self._meta(session_file)
        if "sessionId" not in meta:
            return DetectResult(detected=False, reason="not a recognizable Claude Code transcript")
        version = self._version(meta)
        supported = version in SUPPORTED_TRANSCRIPT_VERSIONS
        return DetectResult(
            detected=True,
            native_version=str(version),
            supported=supported,
            reason="" if supported else f"unsupported Claude Code transcript version {version}",
        )

    def export_semantic(self, workspace: str) -> SemanticBundle:
        session_file = self._session_file(workspace)
        if not session_file:
            raise AdapterCompatibilityError("no Claude Code session found for this workspace")
        meta = self._meta(session_file)
        version = self._version(meta)
        if version not in SUPPORTED_TRANSCRIPT_VERSIONS:
            raise AdapterCompatibilityError(
                f"Claude Code transcript version {version} is not supported "
                f"(supported: {list(SUPPORTED_TRANSCRIPT_VERSIONS)})"
            )

        native_bytes = session_file.read_bytes()
        attachment = Attachment(name=session_file.name, media_type=NATIVE_MEDIA_TYPE, data=native_bytes)

        has_vcs = (Path(workspace) / ".git").exists() if workspace else False
        inventory: dict = {
            "workspace": {
                "name": os.path.basename(workspace.rstrip("/")) or "workspace",
                "media_type": "text/x-workspace-path",
                "digest": "sha256:" + hashlib.sha256(workspace.encode()).hexdigest(),
                "size_bytes": len(workspace.encode()),
            },
            "transcript": attachment.to_manifest_entry(),
            "continuation_prompt": "Continue the Claude Code session from the exported transcript.",
        }
        assert_no_secret_values(inventory, list(os.environ.get("ANTHROPIC_API_KEY", "").split()))

        missing = [] if has_vcs else ["vcs"]
        missing += ["skills", "tools", "environment"]
        return SemanticBundle(
            adapter=self.name,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            fidelity=FidelityReport(
                level="high",
                notes="Transcript-based semantic continuation; not exact memory transfer.",
                missing=missing,
            ),
            inventory=inventory,
            native=[attachment],
        )

    def continuation_launch(self, bundle: SemanticBundle) -> LaunchSpec:
        session_id = self._session_id_from(bundle)
        cmd = ["claude", "--resume", session_id] if session_id else ["claude"]
        return LaunchSpec(command=cmd, working_dir="/work")

    def _session_id_from(self, bundle: SemanticBundle) -> Optional[str]:
        if not bundle.native:
            return None
        first_line = bundle.native[0].data.split(b"\n", 1)[0]
        try:
            return json.loads(first_line).get("sessionId")
        except json.JSONDecodeError:
            return None

    def collect_result(self, workspace: str) -> AdapterResult:
        session_file = self._session_file(workspace)
        summary = (
            f"claude session {self._meta(session_file).get('sessionId')}"
            if session_file else "no claude session"
        )
        return AdapterResult(exit_code=0, artifacts=[], summary=summary)
