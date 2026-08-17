"""Translate real node/runtime capabilities into Host API profiles.

The provider advertises a profile only when the underlying node genuinely
supports it. This is the honesty seam: a fake node with disk-only lifecycle
yields core + pause_resume, never exact snapshot or fork.
"""

from __future__ import annotations

from .node import NodeCapabilities


def host_api_capabilities(node_caps: NodeCapabilities) -> list[str]:
    caps: list[str] = []
    if node_caps.pause_resume:
        caps.append("session.pause_resume")
    if node_caps.memory_snapshot:
        caps.append("session.snapshot.exact")
    # session.fork additionally requires the kernel fork RPC (barista-046), which
    # is not yet part of Contract A — so CoW alone does not advertise fork.
    # capsule.*/grants/story/evaluation are provider/Cloud concerns, not local.
    return caps
