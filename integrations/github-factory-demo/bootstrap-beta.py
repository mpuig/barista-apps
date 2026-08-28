#!/usr/bin/env python3
"""Bootstrap the beta demo with separate keyring bootstrap authority."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
from pathlib import Path

from barista_app_sdk import BaristaClient, Config
from barista_github_factory_demo.bootstrap import setup_demo


def _read_secret(path: Path, label: str) -> str:
    raw = path.expanduser().read_bytes()
    if len(raw) > 16 * 1024:
        raise SystemExit(f"{label} file exceeds 16 KiB")
    value = raw.decode("utf-8").strip()
    if not value or any(character in value for character in ("\n", "\r", "\x00")):
        raise SystemExit(f"{label} must be one non-empty line")
    return value


def _webhook_secret(path: Path) -> str:
    path = path.expanduser()
    if path.exists():
        value = _read_secret(path, "webhook secret")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        value = secrets.token_hex(32)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(value + "\n")
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise SystemExit("webhook secret must be 32 bytes encoded as lowercase hex")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner", default="mpuig")
    parser.add_argument("--repository", default="barista-factory-demo")
    parser.add_argument("--node-host", default="88.99.166.242")
    parser.add_argument("--ssh-key", type=Path, default=Path.home() / ".ssh/barista_hetzner")
    parser.add_argument(
        "--known-hosts",
        type=Path,
        default=Path.home() / ".ssh/known_hosts.barista-deploy",
    )
    parser.add_argument(
        "--host-token-file", type=Path, default=Path.home() / ".config/barista/key"
    )
    parser.add_argument(
        "--webhook-secret-file",
        type=Path,
        default=Path.home() / ".config/barista/github-factory-webhook-secret",
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=Path.home() / ".config/barista/github-factory-demo-state.json",
    )
    args = parser.parse_args()

    bootstrap_token = subprocess.run(
        ["gh", "auth", "token"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not bootstrap_token:
        raise SystemExit("GitHub CLI returned no bootstrap token")
    host_token = _read_secret(args.host_token_file, "Host API token")
    webhook_secret = _webhook_secret(args.webhook_secret_file)
    ssh = [
        "ssh",
        "-i",
        str(args.ssh_key.expanduser()),
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        f"UserKnownHostsFile={args.known_hosts.expanduser()}",
        "-o",
        "ConnectTimeout=10",
        f"root@{args.node_host}",
        "cat /opt/barista-apps/.github-factory-images.json",
    ]
    image_state = json.loads(subprocess.run(ssh, check=True, capture_output=True, text=True).stdout)
    for app in ("factory", "worker"):
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_state[app]["digest"]):
            raise SystemExit(f"managed node returned invalid {app} digest")

    root = Path(__file__).resolve().parents[2]
    with BaristaClient(Config(endpoint="https://beta.barista.sh", token=host_token)) as client:
        state = setup_demo(
            token=bootstrap_token,
            owner=args.owner,
            repository=args.repository,
            webhook_url="https://github-factory.beta.barista.sh/webhooks/github",
            webhook_secret=webhook_secret,
            factory_manifest=root / "apps/factory/manifest.json",
            factory_name="github-demo-factory",
            factory_image=image_state["factory"]["image"],
            factory_digest=image_state["factory"]["digest"],
            worker_manifest=root / "apps/github-issue-worker/manifest.json",
            worker_name="github-issue-worker",
            worker_image=image_state["worker"]["image"],
            worker_digest=image_state["worker"]["digest"],
            state_path=args.state,
            reuse=True,
            client=client,
        )
    print(json.dumps(state, indent=2, sort_keys=True))
    print(f"webhook signing secret retained at {args.webhook_secret_file}; value not printed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
