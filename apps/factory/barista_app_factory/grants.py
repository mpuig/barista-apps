"""Worker grant derivation.

A worker never inherits the coordinator's full authority. It receives a strictly
narrower grant: no child-session creation, only the mission's declared secret
*references* (never raw values), and the mission's egress bounds.

**The coordinator does not mint it.** The authority a worker holds comes from
``permissions.child_sessions`` in ``manifest.json``, and the *provider* mints the
worker's grant from that manifest at child-session create — it is the only party
that can see both levels at once, and a grant that could mint another grant would
be a key that makes keys. What this module computes is the same narrowing applied
to the worker's *environment* (secret references, egress), so a mission is
bounded even on a provider that does not yet advertise ``grants.delegated``.
``WORKER_ACTIONS`` is the declared set, kept identical to the manifest's — a test
fails if the two drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# The coordinator may create/delete workers; a worker may not create children.
WORKER_ACTIONS = ("session.exec", "session.events", "artifact.write")


@dataclass
class WorkerGrant:
    actions: tuple[str, ...]
    secret_refs: dict[str, str]
    egress: dict[str, Any] = field(default_factory=dict)

    def env(self) -> dict[str, str]:
        # Reference-only: the worker sees WHERE to resolve a secret, never its
        # value. A provider with grants.delegated resolves these at the boundary.
        return {f"{name}_REF": ref for name, ref in self.secret_refs.items()}


def derive_worker_grant(mission_permissions: dict) -> WorkerGrant:
    secrets = mission_permissions.get("secrets", []) or []
    refs = {s["name"]: s["ref"] for s in secrets}
    for ref in refs.values():
        if not str(ref).startswith(("secret://", "grant://", "ref://")):
            raise ValueError(f"mission secret must be a reference, got {ref!r}")
    return WorkerGrant(
        actions=WORKER_ACTIONS,
        secret_refs=refs,
        egress=mission_permissions.get("egress", {}),
    )
