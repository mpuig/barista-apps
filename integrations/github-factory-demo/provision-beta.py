#!/usr/bin/env python3
"""Provision beta controller secrets over SSH stdin, never argv or source."""

from __future__ import annotations

import argparse
import os
import re
import secrets
import subprocess
import time
from pathlib import Path

_REPOSITORY = re.compile(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _read_secret(path: Path, label: str) -> str:
    raw = path.expanduser().read_bytes()
    if len(raw) > 16 * 1024:
        raise SystemExit(f"{label} file exceeds 16 KiB")
    try:
        value = raw.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise SystemExit(f"{label} file is not UTF-8") from exc
    if not value or "\n" in value or "\r" in value or "\x00" in value:
        raise SystemExit(f"{label} must be one non-empty line")
    return value


def _environment_line(name: str, value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'{name}="{escaped}"\n'


def provision(
    *,
    repository: str,
    github_token_file: Path,
    host_token_file: Path,
    webhook_secret_file: Path,
    cp_host: str,
    ssh_key: Path,
    known_hosts: Path,
    public_url: str,
) -> None:
    if not _REPOSITORY.fullmatch(repository) or repository.endswith(".git"):
        raise SystemExit("repository must be canonical https://github.com/OWNER/REPO")
    github_token = _read_secret(github_token_file, "GitHub token")
    host_token = _read_secret(host_token_file, "Host API token")
    webhook_secret_file = webhook_secret_file.expanduser()
    if webhook_secret_file.exists():
        webhook_secret = _read_secret(webhook_secret_file, "webhook secret")
    else:
        webhook_secret_file.parent.mkdir(parents=True, exist_ok=True)
        webhook_secret = secrets.token_hex(32)
        descriptor = os.open(webhook_secret_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(webhook_secret + "\n")
    if not re.fullmatch(r"[0-9a-f]{64}", webhook_secret):
        raise SystemExit("webhook secret must be 32 bytes encoded as lowercase hex")

    environment = "".join(
        [
            _environment_line("BARISTA_GITHUB_REPOSITORY", repository),
            _environment_line("BARISTA_GITHUB_WEBHOOK_SECRET", webhook_secret),
            _environment_line("BARISTA_GITHUB_TOKEN", github_token),
            _environment_line("BARISTA_FACTORY_APP", "github-demo-factory@0.1.0"),
            _environment_line("BARISTA_FACTORY_WORKER_APP", "github-issue-worker"),
            _environment_line("BARISTA_GITHUB_BASE_REF", "main"),
            _environment_line("BARISTA_HOST_API_ENDPOINT", "https://beta.barista.sh"),
            _environment_line("BARISTA_HOST_API_TOKEN", host_token),
        ]
    ).encode()

    known_hosts = known_hosts.expanduser()
    known_hosts.parent.mkdir(parents=True, exist_ok=True)
    known_hosts.touch(mode=0o600, exist_ok=True)
    known_hosts.chmod(0o600)
    ssh = [
        "ssh",
        "-i",
        str(ssh_key.expanduser()),
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        f"UserKnownHostsFile={known_hosts}",
        "-o",
        "ConnectTimeout=10",
        f"root@{cp_host}",
    ]
    remote = (
        "set -e; install -d -o root -g root -m 0700 /etc/barista; "
        "umask 077; cat > /etc/barista/github-factory-demo.env.tmp; "
        "chown root:root /etc/barista/github-factory-demo.env.tmp; "
        "chmod 600 /etc/barista/github-factory-demo.env.tmp; "
        "mv /etc/barista/github-factory-demo.env.tmp /etc/barista/github-factory-demo.env; "
        "systemctl restart barista-github-factory-demo.service"
    )
    subprocess.run([*ssh, remote], input=environment, check=True)

    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        local = subprocess.run(
            [*ssh, "curl -fsS http://127.0.0.1:8098/healthz >/dev/null"],
            check=False,
        )
        if local.returncode == 0:
            break
        time.sleep(2)
    else:
        raise SystemExit("controller did not become healthy on loopback")

    import httpx

    response = httpx.get(public_url.rstrip("/") + "/healthz", timeout=30)
    if response.status_code != 200 or response.json().get("repository") != repository.removeprefix(
        "https://github.com/"
    ):
        raise SystemExit("public controller health check failed")
    print(f"controller healthy for {repository}")
    print(f"webhook secret retained at {webhook_secret_file}; its value was not printed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--github-token-file", type=Path, required=True)
    parser.add_argument(
        "--host-token-file",
        type=Path,
        default=Path.home() / ".config/barista/key",
    )
    parser.add_argument(
        "--webhook-secret-file",
        type=Path,
        default=Path.home() / ".config/barista/github-factory-webhook-secret",
    )
    parser.add_argument("--cp-host", default="46.225.59.43")
    parser.add_argument("--ssh-key", type=Path, default=Path.home() / ".ssh/barista_hetzner")
    parser.add_argument(
        "--known-hosts",
        type=Path,
        default=Path.home() / ".ssh/known_hosts.barista-deploy",
    )
    parser.add_argument("--public-url", default="https://github-factory.beta.barista.sh")
    args = parser.parse_args()
    provision(**vars(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
