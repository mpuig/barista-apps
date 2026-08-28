"""Turn bounded issue objective data into a deterministic reviewable change."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

MAX_OBJECTIVE_BYTES = 64 * 1024
MAX_TITLE_CHARS = 500
MAX_BODY_CHARS = 32 * 1024


class ObjectiveError(ValueError):
    """The coordinator-provided objective is absent or malformed."""


def _text(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise ObjectiveError(
            f"issue {field} must be a string of at most {maximum} characters"
        )
    return value


def _issue_uri(value: str) -> str:
    parsed = urlparse(value)
    parts = [part for part in parsed.path.split("/") if part]
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or len(parts) != 4
        or parts[2] != "issues"
        or not parts[3].isdigit()
        or value != f"https://github.com/{'/'.join(parts)}"
    ):
        raise ObjectiveError(
            "BARISTA_OBJECTIVE_URI must be a canonical GitHub issue URL"
        )
    return value


def apply_issue_objective(
    objective: Mapping[str, Any],
    *,
    objective_uri: str,
    workspace: str | Path,
) -> Path:
    """Write one issue record; objective strings are data and are never executed."""
    if not isinstance(objective, Mapping):
        raise ObjectiveError("issue objective must be an object")
    allowed = {"number", "title", "body", "state"}
    if set(objective) - allowed:
        raise ObjectiveError("issue objective contains unsupported fields")
    number = objective.get("number")
    if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
        raise ObjectiveError("issue number must be a positive integer")
    title = _text(objective.get("title"), "title", MAX_TITLE_CHARS)
    body = _text(objective.get("body"), "body", MAX_BODY_CHARS)
    state = _text(objective.get("state"), "state", 16)
    if state not in {"open", "closed"}:
        raise ObjectiveError("issue state must be open or closed")
    uri = _issue_uri(objective_uri)
    if not uri.endswith(f"/issues/{number}"):
        raise ObjectiveError("issue URL and objective number differ")

    root = Path(workspace).expanduser().resolve()
    if not (root / ".git").exists():
        raise ObjectiveError("worker workspace is not a Git checkout")
    destination = root / "issues" / f"issue-{number}.md"
    destination.parent.mkdir(parents=True, exist_ok=True)
    content = (
        f"# Issue {number}: {title}\n\n"
        f"Source: {uri}\n\n"
        f"State: {state}\n\n"
        "## Objective\n\n"
        f"{body.rstrip()}\n"
    )
    destination.write_text(content, encoding="utf-8")
    return destination


def main() -> int:
    path = os.environ.get("BARISTA_OBJECTIVE_PATH")
    uri = os.environ.get("BARISTA_OBJECTIVE_URI")
    if not path or not uri:
        raise SystemExit(
            "github-issue-worker configuration error: BARISTA_OBJECTIVE_PATH and BARISTA_OBJECTIVE_URI are required"
        )
    objective_path = Path(path)
    try:
        with objective_path.open("rb") as stream:
            raw = stream.read(MAX_OBJECTIVE_BYTES + 1)
        if len(raw) > MAX_OBJECTIVE_BYTES:
            raise ObjectiveError("issue objective exceeds worker limit")
        objective = json.loads(raw)
        destination = apply_issue_objective(
            objective,
            objective_uri=uri,
            workspace=Path.cwd(),
        )
    except (OSError, json.JSONDecodeError, ObjectiveError) as exc:
        raise SystemExit(f"github-issue-worker objective error: {exc}") from exc
    print(
        json.dumps(
            {"output": str(destination), "issue": objective["number"]}, sort_keys=True
        )
    )
    return 0
