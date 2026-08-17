"""Common sensitive-data handling.

Manifests, logs, stories, and semantic bundles may carry secret *references* or
redactions but never raw declared secret values. These helpers enforce that: a
declared secret value appearing in a payload is a hard error, and text can be
deterministically redacted before it is logged or published.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

REDACTION = "«redacted»"


class SecretLeak(ValueError):
    """A declared secret value appeared where only references/redactions are allowed."""


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
    needles = [s for s in secret_values if s and len(s) >= 4]
    if not needles:
        return payload
    return json.loads(redact_text(json.dumps(payload), needles))
