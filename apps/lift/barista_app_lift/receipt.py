"""Source classification and transfer receipts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class SourceRef:
    """What is being lifted. A Barista-managed session can be exact-eligible; a
    native process is semantic-only."""

    managed: bool
    session_id: Optional[str] = None
    workspace: Optional[str] = None
    description: str = ""


@dataclass
class Classification:
    exact_capable: bool
    semantic_capable: bool
    reason: str


@dataclass
class TransferReceipt:
    mode: str  # exact | semantic
    status: str  # completed | failed
    source_provider: str
    target_provider: str
    source_disposition: str = "preserved"  # preserved | paused | deleted
    content_id: Optional[str] = None
    lineage_id: Optional[str] = None
    adapter: Optional[str] = None
    adapter_version: Optional[str] = None
    compatibility: Optional[str] = None
    transferred: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    redactions: list[str] = field(default_factory=list)
    target_accepted: bool = False
    target_session_id: Optional[str] = None
    error: Optional[str] = None
    resumable_state: Optional[dict] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
