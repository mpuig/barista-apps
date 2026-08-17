"""Common sensitive-data handling.

Manifests, logs, stories, and semantic bundles may carry secret *references* or
redactions but never raw declared secret values. These helpers enforce that: a
declared secret value appearing in a payload is a hard error, and text can be
deterministically redacted before it is logged or published.
"""

from __future__ import annotations

from typing import Any, Iterable

REDACTION = "«redacted»"


class SecretLeak(ValueError):
    """A declared secret value appeared where only references/redactions are allowed."""


# High-confidence secret shapes, for gating payloads where we do NOT have the
# declared secret values (e.g. a transfer receipt built from adapter output).
import re as _re

_SECRET_PATTERNS = (
    _re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"),
    _re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    _re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    _re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    _re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{16,}\b"),
    _re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


def assert_no_high_confidence_secrets(payload: Any) -> None:
    """Fail closed if any high-confidence secret shape appears in payload.

    Used where declared secret values are unavailable but the payload must still
    be provably secret-free before it is recorded or published (a transfer
    receipt, a log line). Pattern-based, so it catches a live key even when no
    one declared it.
    """
    for text in _walk_strings(payload):
        for pattern in _SECRET_PATTERNS:
            if pattern.search(text):
                raise SecretLeak("a high-confidence secret appeared where none is allowed")


def _walk_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _walk_strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _walk_strings(v)


def assert_no_secret_values(payload: Any, secret_values: Iterable[str]) -> None:
    """Raise SecretLeak if any declared secret value appears anywhere in payload.

    Empty/very short values are ignored to avoid false positives on trivial
    strings; a real credential is never a single character.
    """
    needles = [s for s in secret_values if s and len(s) >= 4]
    if not needles:
        return
    for text in _walk_strings(payload):
        for secret in needles:
            if secret in text:
                raise SecretLeak(f"a declared secret value leaked into a {type(payload).__name__}")


def redact_text(text: str, secret_values: Iterable[str]) -> str:
    for secret in sorted((s for s in secret_values if s and len(s) >= 4), key=len, reverse=True):
        text = text.replace(secret, REDACTION)
    return text


def redact_payload(payload: Any, secret_values: Iterable[str]) -> Any:
    """Recursively redact secret values from a JSON-like structure.

    Operates on the parsed structure (each string value), never on the
    serialized text, so a secret containing quotes, backslashes, or non-ASCII
    is matched in its real form rather than slipping through its JSON-escaped
    representation.
    """
    needles = [s for s in secret_values if s and len(s) >= 4]
    if not needles:
        return payload

    def _redact(value: Any) -> Any:
        if isinstance(value, str):
            return redact_text(value, needles)
        if isinstance(value, dict):
            return {k: _redact(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_redact(v) for v in value]
        if isinstance(value, tuple):
            return tuple(_redact(v) for v in value)
        return value

    return _redact(payload)
