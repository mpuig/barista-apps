"""Run the single-agent repository change operation."""

from __future__ import annotations

import json
import os
import sys

from barista_app_sdk import APP_RUN_ENV, AppRun, BaristaClient, Config, errors

from .runner import execute_change_run


def main() -> int:
    raw = os.environ.get(APP_RUN_ENV)
    if not raw:
        print(f"change-agent configuration error: {APP_RUN_ENV} is required", file=sys.stderr)
        return 2
    try:
        run = AppRun.parse(json.loads(raw))
        config = Config.from_env()
    except (json.JSONDecodeError, errors.InvalidRequestError, ValueError) as exc:
        print(f"change-agent configuration error: {exc}", file=sys.stderr)
        return 2

    with BaristaClient(config) as client:
        result = execute_change_run(client, run)
    print(json.dumps(result.to_document(), sort_keys=True, separators=(",", ":")))
    return 0 if result.to_document()["state"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
