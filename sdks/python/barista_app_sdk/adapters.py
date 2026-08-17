"""Provider-neutral harness adapter interface.

An adapter integrates a coding-agent harness (Pi, Claude Code, Codex, …) without
putting harness-specific fields into the Host API. It detects native state,
exports a semantic bundle, builds a continuation launch, reports capabilities
and fidelity, and collects a result. Harness-native transcript/session formats
are preserved as OPAQUE attachments (bytes + media type) and never normalized
away.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable

SEMANTIC_STATE_SCHEMA_VERSION = "v1alpha1"

# Common, provider-neutral inventory components.
INVENTORY_COMPONENTS = (
    "workspace",
    "vcs",
    "transcript",
    "skills",
    "tools",
    "environment",
    "continuation_prompt",
)


@dataclass
class Attachment:
    """An opaque, content-typed blob. The SDK/provider preserve bytes and media
    type verbatim; they do not interpret them."""

    media_type: str
    data: bytes
    name: Optional[str] = None

    def to_manifest_entry(self) -> dict:
        import hashlib

        entry = {
            "media_type": self.media_type,
            "digest": "sha256:" + hashlib.sha256(self.data).hexdigest(),
            "size_bytes": len(self.data),
        }
        # `name` is optional in the schema; omit it rather than emit null so the
        # attachment validates when unnamed.
        if self.name is not None:
            entry["name"] = self.name
        return entry


@dataclass
class FidelityReport:
    """Honest account of a semantic export. Never claims exact continuation for a
    restarted process."""

    level: str  # exact | high | partial | low
    notes: str = ""
    missing: list[str] = field(default_factory=list)


@dataclass
class DetectResult:
    detected: bool
    native_version: Optional[str] = None
    supported: bool = False
    reason: str = ""


@dataclass
class AdapterCapabilities:
    name: str
    supported_versions: list[str]
    semantic_export: bool
    continuation: bool
    native_media_types: list[str] = field(default_factory=list)
    auth_references: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


@dataclass
class SemanticBundle:
    adapter: str
    created_at: str
    fidelity: FidelityReport
    inventory: dict = field(default_factory=dict)
    native: list[Attachment] = field(default_factory=list)

    def to_document(self) -> dict:
        """Serialize to the semantic-state bundle wire form (bytes carried out of
        band via native attachment digests)."""
        unknown = set(self.inventory) - set(INVENTORY_COMPONENTS)
        if unknown:
            raise ValueError(
                f"unknown inventory component(s): {sorted(unknown)}; "
                f"allowed: {list(INVENTORY_COMPONENTS)}"
            )
        return {
            "schema_version": SEMANTIC_STATE_SCHEMA_VERSION,
            "adapter": self.adapter,
            "created_at": self.created_at,
            "fidelity": {
                "level": self.fidelity.level,
                "notes": self.fidelity.notes,
                "missing": self.fidelity.missing,
            },
            "inventory": self.inventory,
            "native": [a.to_manifest_entry() for a in self.native],
        }


@dataclass
class LaunchSpec:
    command: list[str]
    env: dict[str, str] = field(default_factory=dict)
    working_dir: Optional[str] = None


@dataclass
class AdapterResult:
    exit_code: int
    artifacts: list[dict] = field(default_factory=list)
    summary: str = ""


class AdapterCompatibilityError(RuntimeError):
    """The adapter does not support the detected native state version."""


@runtime_checkable
class Adapter(Protocol):
    name: str

    def detect(self, workspace: str) -> DetectResult: ...

    def capabilities(self) -> AdapterCapabilities: ...

    def export_semantic(self, workspace: str) -> SemanticBundle: ...

    def continuation_launch(self, bundle: SemanticBundle) -> LaunchSpec: ...

    def collect_result(self, workspace: str) -> AdapterResult: ...


class JsonlSessionAdapter:
    """Base for harness adapters whose native state is a JSONL session file.

    Subclasses declare the harness's path layout, version gate, and continuation
    command; the base owns the shared, once-only logic: pick the newest session
    (by mtime, never by lexicographic filename), parse its first-line metadata,
    build an opaque semantic bundle with an honest fidelity report, refuse an
    unsupported version loudly, and derive a continuation launch.
    """

    name: str = "sh.barista.adapter.base"
    native_media_type: str = "application/octet-stream"

    def __init__(self, home):
        from pathlib import Path

        self.home = Path(home)

    # -- hooks a subclass implements ------------------------------------- #
    def _candidate_files(self, workspace: str) -> list:  # -> list[Path]
        raise NotImplementedError

    def _version(self, meta: dict):
        raise NotImplementedError

    def _supported(self, version) -> bool:
        raise NotImplementedError

    def _session_id(self, meta: dict) -> Optional[str]:
        raise NotImplementedError

    def _continuation_command(self, session_id: Optional[str]) -> list:
        raise NotImplementedError

    def _supported_versions(self) -> list[str]:
        return []

    def _auth_references(self) -> list[str]:
        return []

    def _secret_env_values(self) -> list[str]:
        return []

    # -- shared ----------------------------------------------------------- #
    def _latest(self, workspace: str):
        files = self._candidate_files(workspace)
        # Newest by modification time: session filenames are often random UUIDs,
        # so a lexicographic sort would pick an arbitrary (possibly stale) one.
        return max(files, key=lambda p: p.stat().st_mtime) if files else None

    def _meta(self, session_file) -> dict:
        import json

        with session_file.open() as fh:
            return json.loads(fh.readline())

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            name=self.name,
            supported_versions=self._supported_versions(),
            semantic_export=True,
            continuation=True,
            native_media_types=[self.native_media_type],
            auth_references=self._auth_references(),
            limitations=[
                "Semantic continuation replays a transcript; it is not exact "
                "process-memory transfer.",
            ],
        )

    def detect(self, workspace: str) -> DetectResult:
        session_file = self._latest(workspace)
        if not session_file:
            return DetectResult(detected=False, reason=f"no {self.name} session for this workspace")
        version = self._version(self._meta(session_file))
        supported = self._supported(version)
        return DetectResult(
            detected=True,
            native_version=str(version),
            supported=supported,
            reason="" if supported else f"unsupported native version {version}",
        )

    def export_semantic(self, workspace: str) -> SemanticBundle:
        import hashlib
        import time
        from pathlib import Path

        session_file = self._latest(workspace)
        if not session_file:
            raise AdapterCompatibilityError(f"no {self.name} session found for this workspace")
        version = self._version(self._meta(session_file))
        if not self._supported(version):
            raise AdapterCompatibilityError(
                f"native version {version} is not supported by {self.name} "
                f"(supported: {self._supported_versions()})"
            )

        attachment = Attachment(
            name=session_file.name, media_type=self.native_media_type, data=session_file.read_bytes()
        )
        has_vcs = (Path(workspace) / ".git").exists() if workspace else False
        inventory: dict = {
            "workspace": {
                "name": Path(workspace.rstrip("/")).name or "workspace",
                "media_type": "text/x-workspace-path",
                "digest": "sha256:" + hashlib.sha256(workspace.encode()).hexdigest(),
                "size_bytes": len(workspace.encode()),
            },
            "transcript": attachment.to_manifest_entry(),
            "continuation_prompt": f"Continue the {self.name} session from the exported transcript.",
        }
        # Never let a declared secret value ride along in the (non-opaque) inventory.
        from .sensitive import assert_no_secret_values

        assert_no_secret_values(inventory, self._secret_env_values())

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
        import json

        session_id = None
        if bundle.native:
            first = bundle.native[0].data.split(b"\n", 1)[0]
            try:
                session_id = self._session_id(json.loads(first))
            except json.JSONDecodeError:
                session_id = None
        return LaunchSpec(command=self._continuation_command(session_id), working_dir="/work")

    def collect_result(self, workspace: str) -> AdapterResult:
        session_file = self._latest(workspace)
        if not session_file:
            return AdapterResult(exit_code=0, summary=f"no {self.name} session")
        sid = self._session_id(self._meta(session_file))
        return AdapterResult(exit_code=0, summary=f"{self.name} session {sid}")
