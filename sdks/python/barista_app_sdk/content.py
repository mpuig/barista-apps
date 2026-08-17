"""Canonical content addressing.

One definition of the deterministic serialization used for content ids across
the ecosystem — sorted keys, no insignificant whitespace, UTF-8, newline
terminated. Sharing it keeps every content id (receipts, stories, capsule
manifests, golden fixtures) byte-for-byte comparable instead of each site
inventing its own and drifting apart.
"""

from __future__ import annotations

import hashlib
from typing import Any


def canonical_bytes(value: Any) -> bytes:
    return (
        __import__("json").dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def content_id(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()
