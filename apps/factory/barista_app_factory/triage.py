"""Closed, bounded protocol for untrusted issue-triage worker decisions."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal

from barista_app_sdk.content import canonical_bytes
from barista_app_sdk.sensitive import assert_no_high_confidence_secrets

MAX_DECISION_BYTES = 64 * 1024
MAX_SUMMARY_CHARS = 8_000
MAX_CRITERION_CHARS = 2_000
MAX_QUESTION_CHARS = 1_000
MAX_REASON_CHARS = 2_000
_REASON_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class TriageDecisionError(ValueError):
    """The worker output is not a safe canonical triage decision."""


@dataclass(frozen=True)
class TriageDecision:
    state: Literal["ready", "needs_input", "refused"]
    summary: str | None = None
    acceptance_criteria: tuple[str, ...] = ()
    questions: tuple[str, ...] = ()
    reason_code: str | None = None
    message: str | None = None

    @classmethod
    def parse_bytes(cls, raw: bytes) -> TriageDecision:
        if not raw or len(raw) > MAX_DECISION_BYTES:
            raise TriageDecisionError(
                "triage decision size is outside the supported bound"
            )
        try:
            text = raw.decode("utf-8")
            document = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TriageDecisionError("triage decision must be UTF-8 JSON") from exc
        if not isinstance(document, dict):
            raise TriageDecisionError("triage decision must be an object")
        allowed = {
            "schema_version",
            "state",
            "summary",
            "acceptance_criteria",
            "questions",
            "reason_code",
            "message",
        }
        if set(document) - allowed:
            raise TriageDecisionError("triage decision contains unknown fields")
        if document.get("schema_version") != "v1alpha1":
            raise TriageDecisionError("triage decision schema_version must be v1alpha1")
        state = document.get("state")
        if state not in {"ready", "needs_input", "refused"}:
            raise TriageDecisionError("triage decision state is invalid")

        def bounded_string(name: str, limit: int) -> str | None:
            value = document.get(name)
            if value is None:
                return None
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise TriageDecisionError(f"triage decision {name} is invalid")
            return value

        def bounded_list(
            name: str, *, minimum: int, maximum: int, limit: int
        ) -> tuple[str, ...]:
            value = document.get(name, [])
            if not isinstance(value, list) or not minimum <= len(value) <= maximum:
                raise TriageDecisionError(f"triage decision {name} is invalid")
            if any(
                not isinstance(item, str) or not item.strip() or len(item) > limit
                for item in value
            ):
                raise TriageDecisionError(f"triage decision {name} is invalid")
            return tuple(value)

        summary = bounded_string("summary", MAX_SUMMARY_CHARS)
        criteria = bounded_list(
            "acceptance_criteria",
            minimum=1 if state == "ready" else 0,
            maximum=20,
            limit=MAX_CRITERION_CHARS,
        )
        questions = bounded_list(
            "questions",
            minimum=1 if state == "needs_input" else 0,
            maximum=5,
            limit=MAX_QUESTION_CHARS,
        )
        reason_code = bounded_string("reason_code", 64)
        message = bounded_string("message", MAX_REASON_CHARS)
        if state == "ready":
            if (
                summary is None
                or questions
                or reason_code is not None
                or message is not None
            ):
                raise TriageDecisionError("ready decision has inconsistent fields")
        elif state == "needs_input":
            if (
                summary is not None
                or criteria
                or reason_code is not None
                or message is not None
            ):
                raise TriageDecisionError(
                    "needs_input decision has inconsistent fields"
                )
        elif (
            summary is not None
            or criteria
            or questions
            or reason_code is None
            or message is None
            or _REASON_CODE.fullmatch(reason_code) is None
        ):
            raise TriageDecisionError("refused decision has inconsistent fields")

        if raw != canonical_bytes(document):
            raise TriageDecisionError("triage decision is not canonical JSON")
        assert_no_high_confidence_secrets(text)
        return cls(state, summary, criteria, questions, reason_code, message)

    def to_document(self) -> dict:
        document: dict = {"schema_version": "v1alpha1", "state": self.state}
        if self.state == "ready":
            document.update(
                summary=self.summary, acceptance_criteria=list(self.acceptance_criteria)
            )
        elif self.state == "needs_input":
            document["questions"] = list(self.questions)
        else:
            document.update(reason_code=self.reason_code, message=self.message)
        return document

    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.to_document())

    def content_id(self) -> str:
        return "sha256:" + hashlib.sha256(self.canonical_bytes()).hexdigest()
