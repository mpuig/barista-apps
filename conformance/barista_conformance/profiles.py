"""Host API capability profiles.

The core profile is mandatory. Every optional capability is an independently
discoverable, independently tested profile — a provider that advertises one
must pass its cases, and a skip never satisfies an advertised profile.
"""

from __future__ import annotations

CORE = "core"

OPTIONAL_PROFILES = (
    "session.pause_resume",
    "session.snapshot.exact",
    "session.fork",
    "capsule.export",
    "capsule.import",
    "grants.delegated",
    "story.publish",
    "branch.evaluation",
)

ALL_PROFILES = (CORE,) + OPTIONAL_PROFILES
