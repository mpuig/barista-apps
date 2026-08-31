"""One bounded release-gate command for managed Barista demos.

The default profile proves generic lifecycle and a dependency-gated Factory
mission through the public Host API. The preflight profile warms configured
Claude/Pi/Codex apps without model spend; the model profile performs explicit
inference. Neither transports model credentials: those remain provider-resolved
secret references in each installed app manifest.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

BASE = "/v1alpha1"
MAX_OUTPUT_CHARS = 8 * 1024
MAX_AGENT_CHECKS = 8
MAX_AGENT_CONFIG_BYTES = 64 * 1024
_AGENT_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Step:
    name: str
    state: str
    duration_ms: int
    detail: dict[str, Any]


class SmokeFailure(RuntimeError):
    pass


class Report:
    def __init__(self, profile: str):
        self.value: dict[str, Any] = {
            "schema_version": "v1alpha1",
            "run_id": "smoke-" + uuid.uuid4().hex,
            "profile": profile,
            "started_at": _timestamp(),
            "finished_at": None,
            "state": "running",
            "steps": [],
        }

    def step(self, name: str, action: Callable[[], dict[str, Any]]) -> None:
        started = time.monotonic()
        try:
            detail = action()
        except Exception as exc:
            elapsed = int((time.monotonic() - started) * 1000)
            self.value["steps"].append(
                asdict(
                    Step(
                        name=name,
                        state="failed",
                        duration_ms=elapsed,
                        detail={
                            "error_type": type(exc).__name__,
                            "error": str(exc)[:MAX_OUTPUT_CHARS],
                        },
                    )
                )
            )
            raise
        elapsed = int((time.monotonic() - started) * 1000)
        self.value["steps"].append(
            asdict(Step(name=name, state="passed", duration_ms=elapsed, detail=detail))
        )

    def finish(self, state: str) -> dict[str, Any]:
        self.value["finished_at"] = _timestamp()
        self.value["state"] = state
        return self.value


def _pytest_step(nodes: list[str], timeout_s: float) -> dict[str, Any]:
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-o",
        "addopts=",
        "-q",
        "-rs",
        *nodes,
    ]
    completed = subprocess.run(
        command,
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout_s,
        env=os.environ.copy(),
    )
    output = (completed.stdout + completed.stderr)[-MAX_OUTPUT_CHARS:]
    if completed.returncode != 0:
        raise SmokeFailure(f"acceptance returned {completed.returncode}: {output}")
    # A release gate cannot translate "not exercised" into green. Managed tests
    # intentionally skip for an offline developer; this explicit command is the
    # operator opting into infrastructure and therefore treats every skip as a
    # refusal.
    if "SKIPPED [" in output or " skipped" in output:
        raise SmokeFailure(f"managed acceptance was skipped: {output}")
    return {"tests": nodes, "output_tail": output}


def _check_url(name: str, url: str) -> dict[str, Any]:
    if not name or len(name) > 64:
        raise ValueError("URL check name must contain 1-64 characters")
    parsed = urllib.parse.urlsplit(url)
    if (
        len(url) > 2048
        or parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("URL check must be bounded credential-free HTTP(S)")
    request = urllib.request.Request(
        url, headers={"User-Agent": "barista-managed-smoke/1"}
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status = response.status
            body = response.read(4096)
    except (OSError, urllib.error.URLError) as exc:
        raise SmokeFailure(f"{name} is unreachable: {exc}") from exc
    if not 200 <= status < 300:
        raise SmokeFailure(f"{name} returned HTTP {status}")
    return {"url": url, "status": status, "body_bytes_sampled": len(body)}


def _headers(token: str) -> dict[str, str]:
    return {
        "accept": "application/json",
        "authorization": f"Bearer {token}",
    }


def _events(client: httpx.Client, session: str) -> list[dict[str, Any]]:
    response = client.get(
        f"{BASE}/sessions/{session}/events", params={"max_events": 300}
    )
    response.raise_for_status()
    events: list[dict[str, Any]] = []
    for line in response.text.splitlines():
        if line.startswith("data:"):
            events.append(json.loads(line[5:]))
    return events


def _wait_operation(
    client: httpx.Client, operation_id: str, timeout_s: float
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        response = client.get(f"{BASE}/operations/{operation_id}")
        response.raise_for_status()
        operation = response.json()
        if operation.get("done"):
            if operation.get("error"):
                raise SmokeFailure(
                    f"operation {operation_id} failed: {operation['error'].get('code', 'unknown')}"
                )
            return operation
        time.sleep(1)
    raise SmokeFailure(f"operation {operation_id} did not settle")


def _exec_marker(
    client: httpx.Client, session: str, command: list[str], expected: str
) -> dict[str, Any]:
    key = "smoke-" + uuid.uuid4().hex
    response = client.post(
        f"{BASE}/sessions/{session}/exec",
        json={"command": command, "timeout_seconds": 600},
        headers={"Idempotency-Key": key},
    )
    response.raise_for_status()
    handle = response.json()
    operation = _wait_operation(client, handle["operation_id"], 660)
    cursor = handle.get("event_cursor", "")
    chunks: list[str] = []
    import base64

    for event in _events(client, session):
        if (event.get("cursor") or "") <= cursor:
            continue
        if event.get("type") == "exec.stdout":
            chunk = (event.get("data") or {}).get("chunk", "")
            chunks.append(base64.b64decode(chunk).decode(errors="replace"))
        if (
            event.get("type") == "exec.exit"
            and event.get("operation_id") == handle["operation_id"]
        ):
            break
    stdout = "".join(chunks)
    if expected not in stdout:
        raise SmokeFailure(f"model marker {expected!r} was absent from bounded stdout")
    return {
        "operation_id": operation["id"],
        "expected_marker": expected,
        "stdout_bytes": len(stdout.encode()),
    }


def _agent_check(
    endpoint: str, token: str, check: dict[str, Any], run_id: str
) -> dict[str, Any]:
    required = {"name", "app", "command", "expected"}
    if set(check) != required:
        raise ValueError("agent check fields must be name, app, command, expected")
    name, app, command, expected = (
        check["name"],
        check["app"],
        check["command"],
        check["expected"],
    )
    if not isinstance(name, str) or not _AGENT_NAME.fullmatch(name):
        raise ValueError("agent check name has an invalid format")
    if not isinstance(app, str) or not 1 <= len(app) <= 214 or "\x00" in app:
        raise ValueError("agent app identity is empty or exceeds its bound")
    if (
        not isinstance(expected, str)
        or not 1 <= len(expected) <= 256
        or "\x00" in expected
    ):
        raise ValueError("agent expected marker is empty or exceeds its bound")
    if (
        not isinstance(command, list)
        or not 1 <= len(command) <= 64
        or not all(
            isinstance(arg, str) and 0 < len(arg) <= 4096 and "\x00" not in arg
            for arg in command
        )
        or sum(len(arg) for arg in command) > 16 * 1024
    ):
        raise ValueError("agent command exceeds its argv bound")
    session = f"{run_id[:18]}-{name.lower()}"[:63]
    with httpx.Client(
        base_url=endpoint.rstrip("/"), headers=_headers(token), timeout=180
    ) as client:
        created = client.post(
            f"{BASE}/sessions",
            json={"app": app, "name": session},
            headers={"Idempotency-Key": "smoke-" + uuid.uuid4().hex},
        )
        created.raise_for_status()
        try:
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                detail = client.get(f"{BASE}/sessions/{session}")
                detail.raise_for_status()
                if detail.json().get("state") in {"ready", "running"}:
                    break
                if detail.json().get("state") in {"failed", "error", "stopped"}:
                    raise SmokeFailure(f"{name} session failed before readiness")
                time.sleep(2)
            else:
                raise SmokeFailure(f"{name} session did not become ready")
            evidence = _exec_marker(client, session, command, expected)
            paused = client.post(
                f"{BASE}/sessions/{session}/pause",
                headers={"Idempotency-Key": "smoke-" + uuid.uuid4().hex},
            )
            paused.raise_for_status()
            _wait_operation(client, paused.json()["id"], 180)
            resumed = client.post(
                f"{BASE}/sessions/{session}/resume",
                headers={"Idempotency-Key": "smoke-" + uuid.uuid4().hex},
            )
            resumed.raise_for_status()
            _wait_operation(client, resumed.json()["id"], 180)
            return {"agent": name, "app": app, **evidence, "pause_resume": True}
        finally:
            deleted = client.delete(
                f"{BASE}/sessions/{session}",
                headers={"Idempotency-Key": "smoke-" + uuid.uuid4().hex},
            )
            if deleted.status_code == 202:
                _wait_operation(client, deleted.json()["id"], 180)


def _agent_checks() -> list[dict[str, Any]]:
    raw = os.environ.get("BARISTA_MANAGED_SMOKE_AGENT_CHECKS", "")
    if not raw:
        raise ValueError(
            "preflight/model profiles require BARISTA_MANAGED_SMOKE_AGENT_CHECKS; model "
            "credentials must remain provider-side app secret references"
        )
    if len(raw.encode()) > MAX_AGENT_CONFIG_BYTES:
        raise ValueError("agent check configuration exceeds 64 KiB")
    value = json.loads(raw)
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_AGENT_CHECKS:
        raise ValueError("agent checks must contain 1-8 entries")
    if not all(isinstance(check, dict) for check in value):
        raise ValueError("each agent check must be an object")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="barista-managed-smoke")
    parser.add_argument(
        "--profile",
        choices=("default", "preflight", "model", "slow"),
        default="default",
    )
    parser.add_argument("--check-url", action="append", default=[], metavar="NAME=URL")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=float, default=900.0)
    return parser


def _url_checks(arguments: list[str]) -> list[tuple[str, str]]:
    if len(arguments) > 32:
        raise ValueError("at most 32 public URL checks are accepted")
    checks: list[tuple[str, str]] = []
    for argument in arguments:
        name, separator, url = argument.partition("=")
        if not separator or not url.startswith(("http://", "https://")):
            raise ValueError("--check-url must be NAME=http(s)://URL")
        checks.append((name, url))
    return checks


def main() -> int:
    args = _parser().parse_args()
    report = Report(args.profile)
    state = "passed"
    try:
        endpoint = os.environ.get("BARISTA_HOST_API_ENDPOINT", "").strip()
        token = os.environ.get("BARISTA_HOST_API_TOKEN", "").strip()
        if not endpoint or not token:
            raise ValueError(
                "BARISTA_HOST_API_ENDPOINT and BARISTA_HOST_API_TOKEN are required"
            )
        report.step(
            "managed-lifecycle",
            lambda: _pytest_step(
                [
                    "tests/test_managed_acceptance.py::test_managed_session_lifecycle_smoke"
                ],
                args.timeout,
            ),
        )
        report.step(
            "factory-dependency-mission",
            lambda: _pytest_step(
                [
                    "tests/test_managed_acceptance.py::test_a_mission_runs_with_the_coordinator_in_a_session"
                ],
                args.timeout,
            ),
        )
        for name, url in _url_checks(args.check_url):
            report.step(f"public-url:{name}", lambda n=name, u=url: _check_url(n, u))
        if args.profile in {"preflight", "model"}:
            for check in _agent_checks():
                report.step(
                    f"agent:{check['name']}",
                    lambda selected=check: _agent_check(
                        endpoint, token, selected, report.value["run_id"]
                    ),
                )
        if args.profile == "slow":
            report.step(
                "real-grant-renewal",
                lambda: _pytest_step(
                    [
                        (
                            "tests/test_managed_acceptance.py::"
                            "test_mission_renews_a_real_grant_under_elapsed_time"
                        )
                    ],
                    max(args.timeout, 1200),
                ),
            )
    except Exception as exc:  # noqa: BLE001 - CLI boundary records partial report
        state = "failed"
        if not report.value["steps"] or report.value["steps"][-1]["state"] != "failed":
            report.value["steps"].append(
                asdict(
                    Step(
                        name="configuration",
                        state="failed",
                        duration_ms=0,
                        detail={
                            "error_type": type(exc).__name__,
                            "error": str(exc)[:MAX_OUTPUT_CHARS],
                        },
                    )
                )
            )
    document = report.finish(state)
    encoded = json.dumps(document, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(encoded)
        temporary.replace(args.output)
    sys.stdout.write(encoded)
    return 0 if state == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
