"""CLI: run a Factory mission through a Host API provider.

    barista-factory run mission.json \
        --endpoint http://localhost:8088 --state /work/mission-state.json
"""

from __future__ import annotations

import argparse
import json
import sys

from barista_app_sdk import BaristaClient, Config

from .coordinator import Coordinator
from .mission import Mission


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="barista-factory")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="Run a mission to completion.")
    run.add_argument("mission", help="Path to mission.json")
    run.add_argument("--endpoint", help="Host API endpoint (or BARISTA_HOST_API_ENDPOINT).")
    run.add_argument("--token-env", help="Env var holding the bearer token.")
    run.add_argument("--state", default="/work/mission-state.json", help="Durable state path.")
    args = parser.parse_args(argv)

    mission = Mission.load_file(args.mission)
    config = (
        Config(endpoint=args.endpoint.rstrip("/"), token_env=args.token_env)
        if args.endpoint
        else Config.from_env()
    )

    print("coordinator ready", flush=True)  # readiness log line (see manifest)
    with BaristaClient(config) as client:
        coordinator = Coordinator(client, mission, args.state)
        state = coordinator.run()

    summary = state.summary()
    print(json.dumps({"mission": mission.name, "state": state.state, **summary}, indent=1))
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
