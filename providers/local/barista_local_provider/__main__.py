"""CLI: serve the local Host API provider.

Default binds a user-owned Unix socket (single-user, not remotely reachable).
A loopback TCP bind is available for local tooling; any non-loopback bind or a
token-authenticated remote bind must be requested explicitly. The provider
never claims tenant isolation, billing, global placement, or public sharing.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _default_data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(base) / "barista-local-provider"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="barista-local-provider")
    parser.add_argument("--data-dir", default=str(_default_data_dir()))
    parser.add_argument("--socket", help="Unix socket path (default under the data dir).")
    parser.add_argument("--host", help="Bind a TCP host instead of a Unix socket (e.g. 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="Permit a non-loopback TCP bind. Requires --token.",
    )
    parser.add_argument("--token", help="Bearer token required for every request.")
    parser.add_argument("--node-grpc", help="Target a real Node Agent at this gRPC address.")
    args = parser.parse_args(argv)

    if args.host and args.host not in ("127.0.0.1", "::1", "localhost") and not args.allow_remote:
        parser.error("non-loopback --host requires --allow-remote (and --token).")
    if args.allow_remote and not args.token:
        parser.error("--allow-remote requires --token.")

    import uvicorn

    from . import create_local_app

    node = None
    if args.node_grpc:
        from .node.grpc_client import GrpcNodeClient

        node = GrpcNodeClient(args.node_grpc)

    app, store, _node = create_local_app(args.data_dir, node=node, token=args.token)

    if args.host:
        uvicorn.run(app, host=args.host, port=args.port)
    else:
        socket_path = args.socket or str(Path(args.data_dir) / "provider.sock")
        Path(socket_path).parent.mkdir(parents=True, exist_ok=True)
        if os.path.exists(socket_path):
            os.unlink(socket_path)
        # Serve, then tighten the socket to the owning user only.
        os.umask(0o177)
        uvicorn.run(app, uds=socket_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
