"""Pi harness adapter.

Detects Pi native session state, exports it as an opaque semantic bundle with an
honest fidelity report, and builds a continuation launch. Pi-native transcript
bytes are preserved verbatim; no provider-specific fields are introduced.

Pi stores one JSONL session per workspace under
``<home>/sessions/--<cwd-with-slashes-as-dashes>--/<timestamp>_<uuid>.jsonl``.
The first line is a ``{"type":"session","version":N,"id":...,"cwd":...}`` record.
"""

from __future__ import annotations

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

ADAPTER_NAME = "sh.barista.adapter.pi"
NATIVE_MEDIA_TYPE = "application/vnd.pi.session+jsonl"
SUPPORTED_SESSION_VERSIONS = (3,)


def _default_home() -> Path:
    return Path(os.environ.get("PI_HOME", os.path.expanduser("~/.pi/agent")))


def _encode_cwd(workspace: str) -> str:
    return "--" + workspace.strip("/").replace("/", "-") + "--"


class PiAdapter:
    name = ADAPTER_NAME

    def __init__(self, home: Optional[Path] = None):
        self.home = Path(home) if home else _default_home()

    # -- discovery -------------------------------------------------------- #
    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            name=self.name,
            supported_versions=[str(v) for v in SUPPORTED_SESSION_VERSIONS],
            semantic_export=True,
            continuation=True,
            native_media_types=[NATIVE_MEDIA_TYPE],
            auth_references=["secret://model-provider/api-key"],
            limitations=[
                "Semantic continuation replays a transcript; it is not exact process-memory transfer.",
            ],
        )

    def _session_file(self, workspace: str) -> Optional[Path]:
        session_dir = self.home / "sessions" / _encode_cwd(workspace)
        if not session_dir.is_dir():
            return None
        files = sorted(session_dir.glob("*.jsonl"))
        return files[-1] if files else None

    def _meta(self, session_file: Path) -> dict:
        with session_file.open() as fh:
            first = fh.readline()
        return json.loads(first)

    def detect(self, workspace: str) -> DetectResult:
        session_file = self._session_file(workspace)
        if not session_file:
            return DetectResult(detected=False, reason="no Pi session for this workspace")
        meta = self._meta(session_file)
        version = meta.get("version")
        supported = version in SUPPORTED_SESSION_VERSIONS
        return DetectResult(
            detected=True,
            native_version=str(version),
            supported=supported,
            reason="" if supported else f"unsupported Pi session version {version}",
        )

    # -- export ----------------------------------------------------------- #
    def export_semantic(self, workspace: str) -> SemanticBundle:
        session_file = self._session_file(workspace)
        if not session_file:
            raise AdapterCompatibilityError("no Pi session found for this workspace")
        meta = self._meta(session_file)
        version = meta.get("version")
        if version not in SUPPORTED_SESSION_VERSIONS:
            raise AdapterCompatibilityError(
                f"Pi session version {version} is not supported by this adapter "
                f"(supported: {list(SUPPORTED_SESSION_VERSIONS)})"
            )

        native_bytes = session_file.read_bytes()
        attachment = Attachment(
            name=session_file.name, media_type=NATIVE_MEDIA_TYPE, data=native_bytes
        )

        has_vcs = (Path(workspace) / ".git").exists() if workspace else False
        inventory: dict = {
            "workspace": {
                "name": os.path.basename(workspace.rstrip("/")) or "workspace",
                "media_type": "text/x-workspace-path",
                "digest": "sha256:" + __import__("hashlib").sha256(workspace.encode()).hexdigest(),
                "size_bytes": len(workspace.encode()),
            },
            "transcript": attachment.to_manifest_entry(),
            "continuation_prompt": "Continue the Pi session from the exported transcript.",
        }
        # Never let a declared secret value ride along in the (non-opaque) inventory.
        assert_no_secret_values(inventory, list(os.environ.get("MODEL_API_KEY", "").split()))

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

    # -- continuation ----------------------------------------------------- #
    def continuation_launch(self, bundle: SemanticBundle) -> LaunchSpec:
        session_id = self._session_id_from(bundle)
        cmd = ["pi", "--resume", session_id] if session_id else ["pi"]
        return LaunchSpec(command=cmd, working_dir="/work")

    def _session_id_from(self, bundle: SemanticBundle) -> Optional[str]:
        if not bundle.native:
            return None
        first_line = bundle.native[0].data.split(b"\n", 1)[0]
        try:
            return json.loads(first_line).get("id")
        except json.JSONDecodeError:
            return None

    def collect_result(self, workspace: str) -> AdapterResult:
        session_file = self._session_file(workspace)
        summary = f"pi session {self._meta(session_file).get('id')}" if session_file else "no pi session"
        return AdapterResult(exit_code=0, artifacts=[], summary=summary)
