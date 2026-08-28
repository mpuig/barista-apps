"""Classify inert issue context into one closed triage decision."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path

MAX_OBJECTIVE_BYTES = 64 * 1024
MAX_ANSWER_BYTES = 64 * 1024


class ObjectiveError(ValueError):
    """The coordinator-owned triage envelope is malformed."""


def _canonical(document: dict) -> bytes:
    return (
        json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode()


def decide(document: Mapping) -> dict:
    if not isinstance(document, Mapping) or set(document) != {
        "schema_version",
        "issue",
        "attempt",
        "base_commit",
        "answers",
    }:
        raise ObjectiveError("triage objective has invalid fields")
    if document.get("schema_version") != "v1alpha1":
        raise ObjectiveError("triage objective schema is unsupported")
    issue = document.get("issue")
    if not isinstance(issue, Mapping) or set(issue) != {
        "kind",
        "uri",
        "repository_uri",
        "number",
        "title",
        "body",
        "revision",
    }:
        raise ObjectiveError("triage issue is invalid")
    title = issue.get("title")
    body = issue.get("body")
    if (
        issue.get("kind") != "com.github.issue"
        or not isinstance(title, str)
        or not isinstance(body, str)
        or len(title) > 500
        or len(body) > 32 * 1024
    ):
        raise ObjectiveError("triage issue content is invalid")
    attempt = document.get("attempt")
    answers = document.get("answers")
    if (
        not isinstance(attempt, int)
        or isinstance(attempt, bool)
        or not 1 <= attempt <= 100
        or not isinstance(answers, list)
        or len(answers) > 20
    ):
        raise ObjectiveError("triage attempt context is invalid")
    for answer in answers:
        if (
            not isinstance(answer, Mapping)
            or set(answer) - {"comment_id", "body", "prior_result_digest"}
            or not isinstance(answer.get("comment_id"), int)
            or not isinstance(answer.get("body"), str)
            or not answer["body"].strip()
            or len(answer["body"].encode()) > MAX_ANSWER_BYTES
        ):
            raise ObjectiveError("triage answer is invalid")

    lowered = body.casefold()
    if "[barista:refuse]" in lowered:
        return {
            "schema_version": "v1alpha1",
            "state": "refused",
            "reason_code": "objective_refused",
            "message": "The issue explicitly selects the reference refusal case.",
        }
    unclear = "[barista:needs-input]" in lowered or len(body.strip()) < 20
    if unclear and not answers:
        return {
            "schema_version": "v1alpha1",
            "state": "needs_input",
            "questions": [
                "What observable behavior should change, and what behavior must remain compatible?"
            ],
        }
    return {
        "schema_version": "v1alpha1",
        "state": "ready",
        "summary": f"Implement the bounded objective for issue #{issue['number']}: {title}",
        "acceptance_criteria": [
            "The requested observable behavior is implemented.",
            "Repository-owned independent acceptance checks pass.",
            "No trusted command, scope, credential, base, or delivery policy comes from issue text.",
        ],
    }


def main() -> int:
    objective_path = os.environ.get("BARISTA_TRIAGE_OBJECTIVE_PATH")
    result_path = os.environ.get("BARISTA_TRIAGE_RESULT_PATH")
    if not objective_path or not result_path:
        raise SystemExit("triage configuration is incomplete")
    try:
        raw = Path(objective_path).read_bytes()
        if not raw or len(raw) > MAX_OBJECTIVE_BYTES:
            raise ObjectiveError("triage objective size is invalid")
        document = json.loads(raw)
        decision = decide(document)
        output = Path(result_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(_canonical(decision))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ObjectiveError) as exc:
        raise SystemExit(f"triage objective error: {exc}") from exc
    return 0
