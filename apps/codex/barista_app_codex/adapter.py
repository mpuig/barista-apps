"""Codex CLI harness adapter.

Detects Codex native rollout state, exports it as an opaque semantic bundle with
an honest fidelity report, and builds a continuation launch. The native rollout
bytes are preserved verbatim; no provider-specific fields are introduced.

Codex stores date-nested rollout transcripts under
``<home>/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl``. The first line is a
``{"type":"session_meta","payload":{"id":...,"cwd":...,"cli_version":"X.Y.Z"}}``
record; the ``cli_version`` major gates compatibility.
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

ADAPTER_NAME = "sh.barista.adapter.codex"
NATIVE_MEDIA_TYPE = "application/vnd.codex.rollout+jsonl"
SUPPORTED_ROLLOUT_MAJOR_VERSIONS = (0,)


def _default_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", os.path.expanduser("~/.codex")))


def _major(cli_version: str) -> Optional[int]:
    try:
        return int(str(cli_version).split(".")[0])
    except (ValueError, IndexError):
        return None


class CodexAdapter:
    name = ADAPTER_NAME

    def __init__(self, home: Optional[Path] = None):
        self.home = Path(home) if home else _default_home()

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            name=self.name,
            supported_versions=[f"{m}.x" for m in SUPPORTED_ROLLOUT_MAJOR_VERSIONS],
            semantic_export=True,
            continuation=True,
            native_media_types=[NATIVE_MEDIA_TYPE],
            auth_references=["secret://openai/api-key"],
            limitations=[
                "Semantic continuation replays a rollout; it is not exact process-memory transfer.",
            ],
        )

    def _meta(self, session_file: Path) -> dict:
        with session_file.open() as fh:
            return json.loads(fh.readline())

    def _session_file(self, workspace: str) -> Optional[Path]:
        sessions = self.home / "sessions"
        if not sessions.is_dir():
            return None
        matches: list[Path] = []
        for f in sorted(sessions.rglob("rollout-*.jsonl")):
            try:
                payload = self._meta(f).get("payload", {})
            except json.JSONDecodeError:
                continue
            if payload.get("cwd") == workspace:
                matches.append(f)
        return matches[-1] if matches else None

    def detect(self, workspace: str) -> DetectResult:
        session_file = self._session_file(workspace)
        if not session_file:
            return DetectResult(detected=False, reason="no Codex rollout for this workspace")
        payload = self._meta(session_file).get("payload", {})
        cli_version = payload.get("cli_version", "")
        major = _major(cli_version)
        supported = major in SUPPORTED_ROLLOUT_MAJOR_VERSIONS
        return DetectResult(
            detected=True,
            native_version=str(cli_version),
            supported=supported,
            reason="" if supported else f"unsupported Codex cli_version {cli_version}",
        )

    def export_semantic(self, workspace: str) -> SemanticBundle:
        session_file = self._session_file(workspace)
        if not session_file:
            raise AdapterCompatibilityError("no Codex rollout found for this workspace")
        payload = self._meta(session_file).get("payload", {})
        cli_version = payload.get("cli_version", "")
        if _major(cli_version) not in SUPPORTED_ROLLOUT_MAJOR_VERSIONS:
            raise AdapterCompatibilityError(
                f"Codex cli_version {cli_version} is not supported "
                f"(supported majors: {list(SUPPORTED_ROLLOUT_MAJOR_VERSIONS)})"
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
            "continuation_prompt": "Continue the Codex session from the exported rollout.",
        }
        assert_no_secret_values(inventory, list(os.environ.get("OPENAI_API_KEY", "").split()))

        missing = [] if has_vcs else ["vcs"]
        missing += ["skills", "tools", "environment"]
        return SemanticBundle(
            adapter=self.name,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            fidelity=FidelityReport(
                level="high",
                notes="Rollout-based semantic continuation; not exact memory transfer.",
                missing=missing,
            ),
            inventory=inventory,
            native=[attachment],
        )

    def continuation_launch(self, bundle: SemanticBundle) -> LaunchSpec:
        session_id = self._session_id_from(bundle)
        cmd = ["codex", "resume", session_id] if session_id else ["codex"]
        return LaunchSpec(command=cmd, working_dir="/work")

    def _session_id_from(self, bundle: SemanticBundle) -> Optional[str]:
        if not bundle.native:
            return None
        first_line = bundle.native[0].data.split(b"\n", 1)[0]
        try:
            return json.loads(first_line).get("payload", {}).get("id")
        except json.JSONDecodeError:
            return None

    def collect_result(self, workspace: str) -> AdapterResult:
        session_file = self._session_file(workspace)
        if not session_file:
            return AdapterResult(exit_code=0, artifacts=[], summary="no codex session")
        sid = self._meta(session_file).get("payload", {}).get("id")
        return AdapterResult(exit_code=0, artifacts=[], summary=f"codex session {sid}")
