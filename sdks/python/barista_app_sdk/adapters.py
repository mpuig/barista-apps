"""Provider-neutral harness adapter interface.

An adapter integrates a coding-agent harness (Pi, Claude Code, Codex, …) without
putting harness-specific fields into the Host API. It detects native state,
exports a semantic bundle, builds a continuation launch, reports capabilities
and fidelity, and collects a result. Harness-native transcript/session formats
are preserved as OPAQUE attachments (bytes + media type) and never normalized
away.
"""

from __future__ import annotations

import base64
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

        return {
            "name": self.name,
            "media_type": self.media_type,
            "digest": "sha256:" + hashlib.sha256(self.data).hexdigest(),
            "size_bytes": len(self.data),
        }


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
