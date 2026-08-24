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

#: The mission did not finish and no task is to blame: the coordinator lost the
#: authority to act. An operator problem, not a task problem — hence its own
#: code, distinct from 1 ("some task failed").
EXIT_LOST_AUTHORITY = 3


def exit_code_for(state) -> int:
    """Three outcomes, three exit codes, because they send someone to three
    different places: the work is fine (0), a task is broken (1), or the
    coordinator was not allowed to act (3). Reporting a lapsed credential as
    failed work would have someone debugging a task that never ran."""
    if state.authority_lost:
        return EXIT_LOST_AUTHORITY
    return 0 if state.summary()["failed"] == 0 else 1


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
    result = {"mission": mission.name, "state": state.state, **summary}
    if state.credential:
        result["credential"] = state.credential
    if state.authority_lost:
        result["lost_authority"] = state.authority_lost
    print(json.dumps(result, indent=1))
    return exit_code_for(state)


if __name__ == "__main__":
    sys.exit(main())
