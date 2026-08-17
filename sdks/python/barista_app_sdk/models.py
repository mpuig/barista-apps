"""Lightweight typed models parsed from Host API JSON."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Discovery:
    contract_versions: list[str]
    core_profile: bool
    capabilities: list[str]
    limits: dict[str, int] = field(default_factory=dict)
    provider: dict[str, str] = field(default_factory=dict)
    extensions: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def parse(cls, d: dict) -> "Discovery":
        return cls(
            contract_versions=d.get("contract_versions", []),
            core_profile=bool(d.get("core_profile")),
            capabilities=list(d.get("capabilities", [])),
            limits=d.get("limits", {}),
            provider=d.get("provider", {}),
            extensions=d.get("extensions", {}),
        )

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities


@dataclass
class Session:
    id: str
    app: str
    state: str
    created_at: str
    name: Optional[str] = None
    lineage: Optional[dict] = None
    metadata: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    @classmethod
    def parse(cls, d: dict) -> "Session":
        return cls(
            id=d["id"], app=d["app"], state=d["state"], created_at=d["created_at"],
            name=d.get("name"), lineage=d.get("lineage"), metadata=d.get("metadata", {}), raw=d,
        )


@dataclass
class Operation:
    id: str
    kind: str
    done: bool
    session_id: Optional[str] = None
    result: Optional[dict] = None
    error: Optional[dict] = None
    last_event_cursor: Optional[str] = None
    raw: dict = field(default_factory=dict)

    @classmethod
    def parse(cls, d: dict) -> "Operation":
        return cls(
            id=d["id"], kind=d["kind"], done=bool(d["done"]),
            session_id=d.get("session_id"), result=d.get("result"), error=d.get("error"),
            last_event_cursor=d.get("last_event_cursor"), raw=d,
        )


@dataclass
class ExecHandle:
    operation_id: str
    event_cursor: str


@dataclass
class Artifact:
    id: str
    name: str
    digest: str
    size_bytes: int
    media_type: str
    created_at: str

    @classmethod
    def parse(cls, d: dict) -> "Artifact":
        return cls(
            id=d["id"], name=d["name"], digest=d["digest"],
            size_bytes=d["size_bytes"], media_type=d["media_type"], created_at=d["created_at"],
        )


@dataclass
class Event:
    cursor: str
    type: str
    session_id: str
    time: str
    operation_id: Optional[str] = None
    data: dict = field(default_factory=dict)

    @classmethod
    def parse(cls, d: dict) -> "Event":
        return cls(
            cursor=d["cursor"], type=d["type"], session_id=d["session_id"], time=d["time"],
            operation_id=d.get("operation_id"), data=d.get("data", {}),
        )
