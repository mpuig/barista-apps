#!/usr/bin/env python3
"""Trusted beta adapter: exact accepted Git commit -> digest-pinned public service.

The controller invokes this fixed program with source data on stdin. Deployment
configuration and credential file paths come only from operator-owned argv.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

import httpx
from barista_app_sdk import AppRun, BaristaClient, Config

_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def _secret(path: Path) -> str:
    selected = path.expanduser().resolve()
    mode = selected.stat().st_mode & 0o777
    if mode & 0o077:
        raise ValueError("deployment credential file must not be group/world accessible")
    raw = selected.read_bytes()
    if len(raw) > 16 * 1024:
        raise ValueError("deployment credential exceeds 16 KiB")
    value = raw.decode().strip()
    if not value or any(character in value for character in "\r\n\x00"):
        raise ValueError("deployment credential must be one non-empty line")
    return value


def _request(repository: str) -> dict:
    raw = os.read(0, 64 * 1024 + 1)
    if len(raw) > 64 * 1024:
        raise ValueError("deployment request exceeded 64 KiB")
    value = json.loads(raw)
    required = {
        "schema_version",
        "operation_id",
        "program_id",
        "repository",
        "accepted_commit",
        "acceptance",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("deployment request has an invalid shape")
    if value["schema_version"] != "v1alpha1" or value["repository"] != repository:
        raise ValueError("deployment request changed trusted repository identity")
    if not isinstance(value["operation_id"], str) or not value["operation_id"].startswith("ar-"):
        raise ValueError("deployment operation identity is invalid")
    if not isinstance(value["program_id"], str) or _ID.fullmatch(value["program_id"]) is None:
        raise ValueError("deployment program identity is invalid")
    commit = value["accepted_commit"]
    acceptance = value["acceptance"]
    if (
        not isinstance(commit, str)
        or _COMMIT.fullmatch(commit) is None
        or not isinstance(acceptance, dict)
        or acceptance.get("accepted") is not True
        or acceptance.get("exit_code") != 0
        or acceptance.get("assembled_commit") != commit
        or acceptance.get("program") != value["program_id"]
    ):
        raise ValueError("deployment requires the exact independently accepted commit")
    return value


def _run_builder(command: list[str], request: dict, timeout: int) -> dict:
    raw = json.dumps(request, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    result = subprocess.run(  # noqa: S603 - operator-owned fixed builder argv
        command,
        input=raw,
        check=False,
        capture_output=True,
        timeout=timeout,
        env={
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "LANG": "C.UTF-8",
            **({"HOME": os.environ["HOME"]} if "HOME" in os.environ else {}),
        },
    )
    if len(result.stdout) > 64 * 1024 or len(result.stderr) > 64 * 1024:
        raise ValueError("builder output exceeded 64 KiB")
    if result.returncode != 0:
        error = " ".join(result.stderr.decode(errors="replace").split())[:1000]
        raise RuntimeError(f"builder failed ({result.returncode}): {error}")
    value = json.loads(result.stdout)
    if (
        not isinstance(value, dict)
        or set(value) != {"image", "digest"}
        or not isinstance(value["image"], str)
        or len(value["image"]) > 255
        or not isinstance(value["digest"], str)
        or _DIGEST.fullmatch(value["digest"]) is None
    ):
        raise ValueError("builder returned an invalid image identity")
    return value


def _manifest(name: str, image: str, digest: str) -> dict:
    return {
        "schema_version": "v1alpha1",
        "name": name,
        "version": "0.1.0",
        "description": "Factory-generated product deployed after explicit human request.",
        "workload": {
            "image": image,
            "digest": digest,
            "architectures": ["x86_64"],
            "entrypoint": ["python", "-m", "app.server"],
            "working_dir": "/app",
            "readiness": {
                "type": "http",
                "port": 8080,
                "http_path": "/api/health",
                "timeout_seconds": 60,
            },
        },
        "runs": {
            "serve": {
                "lifecycle": "service",
                "input": {
                    "media_type": "application/json",
                    "schema": {
                        "$schema": "https://json-schema.org/draft/2020-12/schema",
                        "type": "object",
                        "additionalProperties": False,
                        "maxProperties": 0,
                    },
                },
                "outputs": {"web": {"kinds": ["sh.barista.endpoint"], "required": True}},
                "deliveries": {"web": {"kinds": ["sh.barista.public-endpoint"]}},
            }
        },
        "endpoints": [{"name": "web", "protocol": "http", "port": 8080}],
        "permissions": {"actions": [], "network": {"egress": "none", "allow": []}},
    }


def deploy(args: argparse.Namespace) -> dict:
    request = _request(args.repository)
    operation_id = request["operation_id"]
    program_id = request["program_id"]
    commit = request["accepted_commit"]
    suffix = hashlib.sha256(operation_id.encode()).hexdigest()[:12]
    app_name = f"product-{program_id}"
    session_name = app_name
    slug = f"{args.slug_prefix}-{program_id}"
    try:
        builder_command = json.loads(args.builder_command_json)
    except json.JSONDecodeError as exc:
        raise ValueError("builder command is not JSON argv") from exc
    if (
        not isinstance(builder_command, list)
        or not builder_command
        or len(builder_command) > 64
        or any(not isinstance(item, str) or not item or len(item) > 4096 for item in builder_command)
        or not Path(builder_command[0]).is_absolute()
    ):
        raise ValueError("builder command must be bounded absolute JSON argv")
    built = _run_builder(builder_command, request, args.build_timeout_seconds)
    image = built["image"]
    digest = built["digest"]

    token = _secret(args.host_token_file)
    manifest = _manifest(app_name, image, digest)
    run = AppRun.parse(
        {
            "schema_version": "v1alpha1",
            "name": session_name,
            "app": app_name,
            "operation": "serve",
            "input": {"media_type": "application/json", "value": {}},
            "deliveries": {
                "web": {
                    "kind": "sh.barista.public-endpoint",
                    "target": f"https://{slug}.{args.public_suffix}",
                    "options": {"executor": "runner"},
                }
            },
            "metadata": {
                "sh.barista.factory-deployment": {
                    "operation_id": operation_id,
                    "program_id": program_id,
                    "commit": commit,
                    "image_digest": digest,
                }
            },
        }
    )
    key = "factory-deploy-" + suffix
    with BaristaClient(Config(endpoint=args.cloud_url, token=token)) as client:
        session, _ = client.launch_app_run(
            run, manifest, install=True, idempotency_key=key
        )
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            current = client.get_session(session.id)
            if current.state == "running":
                break
            if current.state in {"failed", "deleted"}:
                raise RuntimeError(f"deployed service entered {current.state}")
            time.sleep(2)
        else:
            raise TimeoutError("deployed service did not become running")

    with httpx.Client(
        base_url=args.cloud_url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    ) as cloud:
        publication = cloud.post(f"/v1/sessions/{session_name}/publish", json={"slug": slug})
        publication.raise_for_status()
        endpoint = publication.json().get("public_url")
    if endpoint != f"https://{slug}.{args.public_suffix}":
        raise ValueError("publication changed the trusted endpoint identity")
    health = httpx.get(endpoint + "/api/health", timeout=30)
    health.raise_for_status()
    if health.json().get("status") != "ok":
        raise ValueError("generated application health response is not ok")
    return {
        "schema_version": "v1alpha1",
        "operation_id": operation_id,
        "deployment_id": f"deployment-{program_id}",
        "endpoint": endpoint,
        "image_digest": digest,
        "session_name": session_name,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--cloud-url", required=True)
    parser.add_argument("--host-token-file", type=Path, required=True)
    parser.add_argument("--builder-command-json", required=True)
    parser.add_argument("--public-suffix", required=True)
    parser.add_argument("--slug-prefix", default="factory")
    parser.add_argument("--build-timeout-seconds", type=int, default=1200)
    args = parser.parse_args()
    if not 60 <= args.build_timeout_seconds <= 3600:
        raise SystemExit("build timeout outside supported bound")
    result = deploy(args)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
