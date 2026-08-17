"""Versioned, deterministic redaction for Session Stories.

Applies a named/versioned policy to text records, replacing high-confidence
secrets and PII with stable markers and reporting what was removed by category.
Redaction is deterministic (same input + policy -> same output) and fails closed:
if a high-confidence secret survives, or an unknown required media type appears,
a publishable story cannot be produced.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

POLICY_NAME = "default"
POLICY_VERSION = "1"

MARKER = "\u00abredacted:{category}\u00bb"

# High-confidence secret / credential / PII detectors. Order matters: the first
# match category wins for a given span.
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("credential", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)),
    ("secret", re.compile(r"\bsk-[A-Za-z0-9]{16,}\b")),
    ("secret", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("secret", re.compile(r"\bghp_[A-Za-z0-9]{20,}\b")),
    ("secret", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("credential", re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key)\s*[=:]\s*\S+")),
    ("secret", re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{16,}\b")),
    ("pii", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
]

# Media types a story record may carry. Unknown required media fails closed.
KNOWN_MEDIA = {
    "text/plain",
    "text/markdown",
    "text/x-diff",
    "application/json",
    "application/vnd.barista.factory.receipt+json",
}


class RedactionError(ValueError):
    """A publishable story cannot be produced (residual secret or unknown media)."""


@dataclass
class RedactionResult:
    text: str
    removed: Counter


def redact_text(text: str) -> RedactionResult:
    removed: Counter = Counter()

    def _sub(category: str):
        def repl(_m: re.Match) -> str:
            removed[category] += 1
            return MARKER.format(category=category)
        return repl

    out = text
    for category, pattern in _PATTERNS:
        out = pattern.sub(_sub(category), out)
    return RedactionResult(text=out, removed=removed)


def assert_no_residual_secret(text: str) -> None:
    """Fail closed: after redaction no high-confidence secret may remain."""
    for category, pattern in _PATTERNS:
        if category in ("secret", "credential") and pattern.search(text):
            raise RedactionError(f"unresolved high-confidence {category} in a story record")


def check_media(media_type: str) -> None:
    if media_type not in KNOWN_MEDIA:
        raise RedactionError(f"unknown required media type {media_type!r}; refusing to publish")
