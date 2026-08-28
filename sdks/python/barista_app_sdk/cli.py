"""Provider-neutral command-line runner for manifest-declared App Runs."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from .client import BaristaClient
from .config import Config
from .errors import HostAPIError
from .lifecycle import CollectedAppRun
from .resolution import resolve_app
from .runs import AppRun, RunOperation, canonical_bytes


def _named_json(values: list[str], label: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in values:
        name, separator, encoded = item.partition("=")
        if not separator or not name or not encoded:
            raise ValueError(f"{label} must use NAME=JSON")
        if name in result:
            raise ValueError(f"duplicate {label} name {name!r}")
        try:
            value = json.loads(encoded)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{label} {name!r} is not valid JSON: {exc.msg}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"{label} {name!r} JSON must be an object")
        result[name] = value
    return result


def _named_strings(values: list[str], label: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in values:
        name, separator, value = item.partition("=")
        if not separator or not name or not value:
            raise ValueError(f"{label} must use NAME=REFERENCE")
        if name in result:
            raise ValueError(f"duplicate {label} name {name!r}")
        result[name] = value
    return result


def _read_input(source: str) -> Any:
    try:
        raw = sys.stdin.buffer.read() if source == "-" else Path(source).read_bytes()
    except OSError as exc:
        raise ValueError(f"cannot read input {source!r}: {exc.strerror or exc}") from exc
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"input {source!r} is not UTF-8 JSON") from exc


def _operation(manifest: dict, requested: str | None) -> RunOperation:
    runs = manifest.get("runs", {})
    if requested is None:
        if len(runs) != 1:
            choices = ", ".join(sorted(runs)) or "none"
            raise ValueError(f"--operation is required; declared operations: {choices}")
        requested = next(iter(runs))
    # AppRun validation will repeat this lookup after the full envelope exists;
    # doing it now only supplies the declared media type and lifecycle.
    return RunOperation.from_manifest(manifest, requested)


def _default_name(app: str, operation: str, input_value: Any, bindings: dict) -> str:
    seed = canonical_bytes(
        {"app": app, "operation": operation, "input": input_value, "bindings": bindings}
    )
    suffix = hashlib.sha256(seed).hexdigest()[:12]
    base = f"{app.split('@', 1)[0]}-{operation}".lower()
    safe = "".join(char if char.isalnum() or char == "-" else "-" for char in base)
    safe = "-".join(part for part in safe.split("-") if part)[:60].strip("-") or "app-run"
    return f"{safe}-{suffix}"[:80].rstrip("-")


def _config(args) -> Config:
    if args.endpoint:
        return Config(
            endpoint=args.endpoint.rstrip("/"),
            token_env=args.token_env or "BARISTA_HOST_API_TOKEN",
            timeout_seconds=args.request_timeout,
        )
    config = Config.from_env()
    config.timeout_seconds = args.request_timeout
    if args.token_env:
        config.token = None
        config.token_env = args.token_env
    return config


def _run(args) -> int:
    if args.detach and args.cleanup:
        raise ValueError("--detach and --cleanup cannot be combined")
    input_value = _read_input(args.input)
    bindings = _named_json(args.bind, "binding")
    secrets = _named_strings(args.secret, "secret")
    deliveries = _named_json(args.deliver, "delivery")

    with BaristaClient(_config(args)) as client:
        resolved = resolve_app(client, args.app, allow_dirty=args.development)
        manifest = resolved.manifest_document()
        operation = _operation(manifest, args.operation)
        name = args.name or _default_name(
            resolved.reference, operation.name, input_value, bindings
        )
        run = AppRun.parse(
            {
                "schema_version": "v1alpha1",
                "name": name,
                "app": resolved.reference,
                "operation": operation.name,
                "input": {
                    "media_type": args.input_media_type or operation.input_media_type,
                    "value": input_value,
                },
                "bindings": bindings,
                "secrets": secrets,
                "deliveries": deliveries,
                "metadata": {
                    "sh.barista.app-source": {
                        "name": resolved.name,
                        "version": resolved.version,
                        "source": resolved.source,
                        "source_revision": resolved.source_revision,
                        "manifest_digest": resolved.manifest_digest,
                        "workload_digest": resolved.workload_digest,
                    }
                },
            }
        )
        envelope = run.canonical_bytes()
        if args.emit_envelope:
            destination = Path(args.emit_envelope).expanduser()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(envelope)

        session, declared = client.launch_app_run(
            run, manifest, install=not resolved.installed
        )
        receipt: dict[str, Any] = {
            "envelope": run.to_document(),
            "content_id": run.content_id(),
            "session_id": session.id,
            "lifecycle": declared.lifecycle,
        }
        if args.detach:
            receipt["state"] = "launched"
            print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
            return 0

        observed = client.wait_app_run(
            run,
            session,
            declared,
            output=args.output,
            cleanup=args.cleanup,
            timeout=args.timeout,
            poll=args.poll,
            max_result_bytes=args.max_result_bytes,
            expected_identity={
                "name": resolved.name,
                "version": resolved.version,
                "workload_digest": resolved.workload_digest,
                "manifest_digest": resolved.manifest_digest,
                "source": resolved.source,
                "source_revision": resolved.source_revision,
            },
        )
        if isinstance(observed, CollectedAppRun):
            result = observed.result.to_document()
            receipt.update(
                {
                    "state": "completed",
                    "result": result,
                    "result_digest": observed.artifact.digest,
                    "output": str(observed.output_path) if observed.output_path else None,
                    "session_deleted": observed.session_deleted,
                }
            )
            print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
            return 0 if result["state"] == "succeeded" else 1

        receipt.update({"state": "ready", "session_state": observed.state})
        print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
        return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="barista-app")
    subcommands = parser.add_subparsers(dest="command", required=True)
    run = subcommands.add_parser("run", help="Resolve, validate, and launch a typed App Run.")
    run.add_argument("--app", required=True, help="Installed app[@version] or local manifest path.")
    run.add_argument("--operation", help="Manifest operation; inferred when exactly one is declared.")
    run.add_argument("--name", help="Stable run/session name; otherwise derived from canonical input.")
    run.add_argument("--input", required=True, help="JSON input file, or - for stdin.")
    run.add_argument("--input-media-type", help="Override only when it matches the operation declaration.")
    run.add_argument(
        "--bind", action="append", default=[], metavar="NAME=JSON",
        help='Named binding, e.g. workspace={"kind":"sh.barista.git.repository","uri":"file:///repo"}.',
    )
    run.add_argument(
        "--secret", action="append", default=[], metavar="NAME=REFERENCE",
        help="Reference-only secret alias; raw values are rejected by the App Run contract.",
    )
    run.add_argument(
        "--deliver", action="append", default=[], metavar="NAME=JSON",
        help="Explicit named delivery request as JSON.",
    )
    run.add_argument("--output", help="Persist verified terminal result bytes at this local path.")
    run.add_argument("--emit-envelope", help="Also write the exact canonical launch envelope here.")
    run.add_argument("--detach", action="store_true", help="Return after idempotent session launch.")
    run.add_argument(
        "--cleanup", action="store_true",
        help="Delete the owning session only after successful result collection and persistence.",
    )
    run.add_argument(
        "--development", action="store_true",
        help="Explicitly allow a dirty local app source; never changes the pinned workload digest.",
    )
    run.add_argument("--endpoint", help="Host API endpoint (or BARISTA_HOST_API_ENDPOINT).")
    run.add_argument("--token-env", help="Environment variable containing the bearer credential.")
    run.add_argument("--timeout", type=float, default=600.0, help="Lifecycle timeout in seconds.")
    run.add_argument("--poll", type=float, default=0.5, help="Lifecycle polling interval in seconds.")
    run.add_argument("--request-timeout", type=float, default=30.0)
    run.add_argument("--max-result-bytes", type=int, default=4 * 1024 * 1024)
    run.set_defaults(handler=_run)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (HostAPIError, ValueError) as exc:
        print(f"barista-app: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("barista-app: interrupted; owning session was not cleaned up", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
