"""Barista local Host API provider.

A single-user reference implementation of the Host API core profile over a local
Barista Node Agent. No Barista Cloud, no proprietary dependency.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .app import CONTRACT_VERSION, PROVIDER_NAME, PROVIDER_VERSION, LocalProvider, build_app
from .node import FakeNodeClient, NodeClient
from .store import Store


def create_local_app(
    data_dir: str | Path,
    *,
    node: Optional[NodeClient] = None,
    token: Optional[str] = None,
):
    """Build the ASGI app with a durable store under ``data_dir``.

    Defaults to the in-memory fake node backend (persisted under the data dir),
    which is the useful single-machine/offline default. Pass a GrpcNodeClient
    to target a real Node Agent.
    """
    store = Store(Path(data_dir))
    if node is None:
        node = FakeNodeClient(state_path=store.node_state_path)
    app = build_app(node, store, token=token)
    return app, store, node


__all__ = [
    "create_local_app",
    "build_app",
    "LocalProvider",
    "Store",
    "FakeNodeClient",
    "NodeClient",
    "PROVIDER_NAME",
    "PROVIDER_VERSION",
    "CONTRACT_VERSION",
]
