#!/usr/bin/env python3
"""The App Manifest rules that JSON Schema **cannot** express.

`schema.json` is the wire contract: shape, vocabulary, and the constraints a
structural validator can carry. It does not — and cannot — check a rule that
relates one part of a document to another, and the most important rule in
`permissions.child_sessions` is exactly that kind:

    a child session's actions MUST be a subset of the app's own.

A JSON Schema validator will happily accept a manifest that hands its children
`session.delete` when the app itself holds nothing of the sort. An implementer
who assumes the schema stops that will ship the hole. This module is the
reference implementation of what the schema leaves to the *provider*, to be run
at install, before any side effect (design D3):

    python3 contracts/app-manifest/v1alpha1/rules.py apps/factory/manifest.json

Stdlib only, no JSON Schema dependency: validate shape with `schema.json` first,
then these rules. Every violation names the offending actions, because "your
manifest is invalid" is not a usable answer to "which action exceeded what?".
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

OWN_SESSION = "own_session"
CREATED_SESSIONS = "created_sessions"

#: `session.create` is collection-level: it authorizes creating a child session
#: and is bounded by `child_sessions`, not by a scope. `schema.json` rejects the
#: object form for it, so it only ever arrives as a bare action id.
COLLECTION_ACTIONS = frozenset({"session.create"})


@dataclass(frozen=True, order=True)
class Grant:
    """One (action, scope) pair — the unit a provider mints a selector for."""

    action: str
    scope: str

    def __str__(self) -> str:
        if self.action in COLLECTION_ACTIONS:
            return self.action
        return f"{self.action}@{self.scope}"


@dataclass(frozen=True)
class Violation:
    rule: str
    message: str
    actions: tuple[str, ...] = ()

    def __str__(self) -> str:
        named = f" [{', '.join(self.actions)}]" if self.actions else ""
        return f"{self.rule}: {self.message}{named}"


def normalize(entries: Iterable[Any] | None) -> list[Grant]:
    """Normalize a `permissions.actions`-shaped list into (action, scope) pairs.

    A bare action id is exactly `{"action": <id>, "scope": "own_session"}` — the
    backward-compatible reading of every manifest written before scopes existed.
    Order is preserved so error messages follow the author's own ordering.
    """
    out: list[Grant] = []
    for entry in entries or ():
        if isinstance(entry, str):
            out.append(Grant(entry, OWN_SESSION))
        elif isinstance(entry, dict) and "action" in entry:
            out.append(Grant(entry["action"], entry.get("scope", OWN_SESSION)))
        else:  # shape is schema.json's job; ignore what it would have rejected
            continue
    return out


def check_manifest(manifest: dict) -> list[Violation]:
    """Return every semantic violation in `manifest`, in reporting order.

    An empty list means the manifest is *semantically* clean. It says nothing
    about its shape — run `schema.json` for that.
    """
    permissions = manifest.get("permissions") or {}
    app_grants = normalize(permissions.get("actions"))
    child = permissions.get("child_sessions") or {}
    child_grants = normalize(child.get("actions"))
    allow_descendants = bool(child.get("allow_descendants", False))

    violations: list[Violation] = []
    violations += _check_no_duplicate_scope("permissions.actions", app_grants)
    violations += _check_no_duplicate_scope(
        "permissions.child_sessions.actions", child_grants
    )

    if not child_grants:
        # No declared child authority: nothing left to relate. This is the
        # pre-change meaning of a counts-only `child_sessions` block.
        return violations

    app_actions = {g.action for g in app_grants}
    app_created = {g.action for g in app_grants if g.scope == CREATED_SESSIONS}

    # R1 — subset. The rule the schema cannot express.
    excess = sorted({g.action for g in child_grants} - app_actions)
    if excess:
        violations.append(
            Violation(
                "child_actions_exceed_app",
                "permissions.child_sessions.actions declares actions the app "
                "itself does not declare in permissions.actions, so the app "
                "would be delegating authority it does not hold",
                tuple(excess),
            )
        )

    # R2 — a child scoped to ITS created sessions means grandchildren. That
    # needs the app's explicit consent to a third level, and the app may only
    # delegate downwards a verb it exercises downwards itself.
    downward = sorted({g.action for g in child_grants if g.scope == CREATED_SESSIONS})
    if downward and not allow_descendants:
        violations.append(
            Violation(
                "child_created_scope_without_descendants",
                "a child action scoped to 'created_sessions' can only apply to "
                "sessions the child creates, which requires "
                "child_sessions.allow_descendants: true",
                tuple(downward),
            )
        )
    not_held_downward = sorted(a for a in downward if a not in app_created)
    if not_held_downward:
        violations.append(
            Violation(
                "child_created_scope_exceeds_app_scope",
                "a child may only be given 'created_sessions' authority for an "
                "action the app itself declares over its own created sessions",
                tuple(not_held_downward),
            )
        )

    # R3 — coherence between the descendant flag and session.create. schema.json
    # enforces this too; repeated here so this module is usable on its own.
    creates = sorted(
        {g.action for g in child_grants if g.action in COLLECTION_ACTIONS}
    )
    if creates and not allow_descendants:
        violations.append(
            Violation(
                "child_create_without_descendants",
                "permissions.child_sessions.actions grants session creation "
                "while allow_descendants is not true",
                tuple(creates),
            )
        )

    return violations


def _check_no_duplicate_scope(where: str, grants: Sequence[Grant]) -> list[Violation]:
    """`uniqueItems` cannot see that `"session.exec"` and
    `{"action": "session.exec", "scope": "own_session"}` are the same grant."""
    seen: set[Grant] = set()
    dupes: list[str] = []
    for g in grants:
        if g in seen and str(g) not in dupes:
            dupes.append(str(g))
        seen.add(g)
    if not dupes:
        return []
    return [
        Violation(
            "duplicate_action_scope",
            f"{where} declares the same action twice for the same scope "
            "(a bare action id is identical to scope 'own_session')",
            tuple(dupes),
        )
    ]


def main(argv: Sequence[str]) -> int:
    if not argv:
        print(f"usage: {Path(sys.argv[0]).name} <manifest.json> [...]", file=sys.stderr)
        return 2
    failed = False
    for arg in argv:
        path = Path(arg)
        violations = check_manifest(json.loads(path.read_text()))
        if violations:
            failed = True
            print(f"{path}: REFUSED")
            for v in violations:
                print(f"  - {v}")
        else:
            print(f"{path}: OK")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
