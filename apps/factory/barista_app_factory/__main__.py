"""CLI: run a Factory mission through a Host API provider.

    barista-factory run mission.json \
        --endpoint http://localhost:8088 --state /work/mission-state.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from barista_app_sdk import (
    APP_RUN_ENV,
    APP_SESSION_ID_ENV,
    AppRun,
    BaristaClient,
    Config,
    errors,
)

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


#: The mission may arrive in the environment rather than on disk, because of how
#: a coordinator actually starts. A session's declared environment reaches the
#: workload process, and `exec` into that session does NOT inherit it — verified
#: against production: the grant is present in the entrypoint process's environ
#: and absent from an exec'd shell. So the coordinator has to run *as* the
#: entrypoint to hold its credential at all, and an entrypoint cannot be handed a
#: file that nothing has written yet. The environment is the one channel that
#: carries both the credential and the work, set together at session create.
MISSION_ENV = "BARISTA_FACTORY_MISSION"
FACTORY_RUN_OPERATION = "mission"
FACTORY_MISSION_MEDIA_TYPE = "application/vnd.barista.factory.mission+json"


def _map_app_run_to_mission() -> str | None:
    """Adapt the shared run envelope to Factory's established bootstrap.

    `$BARISTA_FACTORY_MISSION` remains the canonical in-session mission
    mechanism. The generic runner sets `$BARISTA_APP_RUN`; this adapter validates
    the Factory-facing portion and writes the mission variable before the
    coordinator is constructed. Nothing silently changes an explicitly supplied
    path or an already supplied mission.
    """
    raw = os.environ.get(APP_RUN_ENV)
    if not raw:
        return None
    try:
        document = json.loads(raw)
        run = AppRun.parse(document)
    except (json.JSONDecodeError, errors.InvalidRequestError, TypeError, ValueError) as exc:
        # Keep startup output concise instead of turning invalid user input into
        # a provider-level guest-unreachable symptom.
        raise SystemExit(f"${APP_RUN_ENV} is not a valid App Run: {exc}") from exc

    if run.operation != FACTORY_RUN_OPERATION:
        raise SystemExit(
            f"${APP_RUN_ENV} selects operation {run.operation!r}; "
            f"Factory supports {FACTORY_RUN_OPERATION!r}"
        )
    if run.input_media_type != FACTORY_MISSION_MEDIA_TYPE:
        raise SystemExit(
            f"${APP_RUN_ENV} input media type must be {FACTORY_MISSION_MEDIA_TYPE}"
        )
    if run.bindings or run.secrets or run.deliveries:
        raise SystemExit(
            f"Factory operation {FACTORY_RUN_OPERATION!r} does not accept bindings, "
            "run secrets, or deliveries"
        )

    mission = run.to_document()["input"]["value"]
    encoded = json.dumps(mission, sort_keys=True, separators=(",", ":"))
    os.environ[MISSION_ENV] = encoded
    return encoded


def _load_mission(path: str | None) -> Mission:
    """The mission from `path`, or from the environment when no path is given.

    Explicit rather than a fallback: a path that is given and missing is an
    error, never silently replaced by whatever the environment happens to hold.
    Getting that backwards would let a stale environment run instead of the
    mission the operator named.
    """
    if path:
        return Mission.load_file(path)
    raw = os.environ.get(MISSION_ENV)
    if not raw:
        raw = _map_app_run_to_mission()
    if not raw:
        raise SystemExit(
            f"no mission: pass a path, or set ${MISSION_ENV} to the mission JSON"
        )
    try:
        return Mission.load(json.loads(raw))
    except json.JSONDecodeError as e:
        raise SystemExit(f"${MISSION_ENV} is not valid JSON: {e}") from e


def _load_config(endpoint: str | None, token_env: str | None) -> Config:
    """Turn missing startup configuration into an app-level report.

    `Config.from_env()` raises ValueError. Left uncaught, that becomes a
    traceback followed by a provider-level unreachable-guest symptom after the
    workload exits. SystemExit keeps the non-zero exit while making the last
    line name the configuration the operator must supply.
    """
    if endpoint:
        return Config(endpoint=endpoint.rstrip("/"), token_env=token_env)
    try:
        return Config.from_env()
    except ValueError as exc:
        raise SystemExit(f"factory configuration error: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="barista-factory")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="Run a mission to completion.")
    run.add_argument(
        "mission",
        nargs="?",
        help=f"Path to mission.json. Omit it to read the mission from ${MISSION_ENV}.",
    )
    run.add_argument("--endpoint", help="Host API endpoint (or BARISTA_HOST_API_ENDPOINT).")
    run.add_argument("--token-env", help="Env var holding the bearer token.")
    run.add_argument("--state", default="/work/mission-state.json", help="Durable state path.")
    args = parser.parse_args(argv)

    mission = _load_mission(args.mission)
    config = _load_config(args.endpoint, args.token_env)

    print("coordinator ready", flush=True)  # readiness log line (see manifest)
    with BaristaClient(config) as client:
        coordinator = Coordinator(
            client,
            mission,
            args.state,
            coordinator_session_id=os.environ.get(APP_SESSION_ID_ENV),
        )
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
