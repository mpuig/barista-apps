"""Capsule transfer client for exact Lift.

Exact transfer moves an exact-memory capsule between hosts. The concrete client
over the Host API's capsule endpoints lands with the kernel capsule work
(barista-046) and the corresponding Host API additions; until then Lift talks to
this protocol, and tests exercise the exact flow against an in-memory fake that
stands in for an exact-capable provider.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Optional, Protocol


class CapsuleIncompatible(RuntimeError):
    """The target cannot restore this capsule exactly (cpu/template/bundle mismatch)."""


class CapsuleError(RuntimeError):
    pass


@dataclass
class Capsule:
    capsule_id: str
    source_session_id: str
    compat_key: str
    lineage_id: str
    size_bytes: int


@dataclass
class ImportedCapsule:
    capsule_id: str
    verified: bool


class CapsuleClient(Protocol):
    def export(self, session_id: str) -> Capsule: ...

    def verify(self, capsule: Capsule) -> bool: ...

    def import_capsule(self, capsule: Capsule) -> ImportedCapsule: ...

    def restore(self, capsule: Capsule, target_name: str) -> str: ...

    def target_compat_key(self) -> str: ...


@dataclass
class FakeCapsuleClient:
    """In-memory exact-capable provider stand-in for tests.

    ``compat_key`` models the target's cpu-class/template/bundle identity; an
    export whose key differs is refused on import (no cold-boot fallback).
    ``fail_on`` injects a fault at a named step to test interrupted transfers.
    """

    compat_key: str = "cpu-x/template-a/bundle-1"
    _store: dict[str, bytes] = field(default_factory=dict)
    fail_on: Optional[str] = None
    export_compat_key: Optional[str] = None

    def export(self, session_id: str) -> Capsule:
        if self.fail_on == "export":
            raise CapsuleError("export failed")
        payload = f"memory-of:{session_id}".encode()
        cid = "sha256:" + hashlib.sha256(payload).hexdigest()
        self._store[cid] = payload
        return Capsule(
            capsule_id=cid, source_session_id=session_id,
            compat_key=self.export_compat_key or self.compat_key,
            lineage_id="lin-" + session_id, size_bytes=len(payload),
        )

    def verify(self, capsule: Capsule) -> bool:
        if self.fail_on == "verify":
            return False
        return capsule.capsule_id in self._store

    def import_capsule(self, capsule: Capsule) -> ImportedCapsule:
        if self.fail_on == "import":
            raise CapsuleError("target import failed")
        if capsule.compat_key != self.compat_key:
            raise CapsuleIncompatible(
                f"capsule compat {capsule.compat_key} != target {self.compat_key}"
            )
        return ImportedCapsule(capsule_id=capsule.capsule_id, verified=True)

    def restore(self, capsule: Capsule, target_name: str) -> str:
        if self.fail_on == "restore":
            raise CapsuleError("restore failed")
        return "sess-" + hashlib.sha256((capsule.capsule_id + target_name).encode()).hexdigest()[:16]

    def target_compat_key(self) -> str:
        return self.compat_key
