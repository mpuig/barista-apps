#!/usr/bin/env python3
"""Provision beta controller secrets over SSH stdin, never argv or source."""

from __future__ import annotations

import argparse
import json
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
    project_token_file: Path | None,
    project_number: int | None,
    project_owner: str | None,
    project_owner_kind: str,
    activity_token_file: Path | None,
    activity_endpoint: str,
    activity_source_url: str,
    activity_deploy_command: str | None,
    cp_host: str,
    ssh_key: Path,
    known_hosts: Path,
    public_url: str,
) -> None:
    if not _REPOSITORY.fullmatch(repository) or repository.endswith(".git"):
        raise SystemExit("repository must be canonical https://github.com/OWNER/REPO")
    github_token = _read_secret(github_token_file, "GitHub token")
    host_token = _read_secret(host_token_file, "Host API token")
    if (project_token_file is None) != (project_number is None):
        raise SystemExit(
            "project token file and project number must be supplied together"
        )
    project_token = (
        _read_secret(project_token_file, "GitHub project token")
        if project_token_file is not None
        else None
    )
    if project_token is not None and project_token == github_token:
        raise SystemExit("forge and project authority must use separate credentials")
    activity_token = (
        _read_secret(activity_token_file, "activity source token")
        if activity_token_file is not None
        else None
    )
    if activity_token is not None and activity_token in {
        github_token,
        project_token,
        host_token,
    }:
        raise SystemExit(
            "activity, forge, project, and Host API authority must use separate credentials"
        )
    deploy_argv = None
    if activity_deploy_command is not None:
        if activity_token is None:
            raise SystemExit("activity deployment requires an activity source token")
        try:
            deploy_argv = json.loads(activity_deploy_command)
        except json.JSONDecodeError as exc:
            raise SystemExit("activity deploy command must be JSON argv") from exc
        if (
            not isinstance(deploy_argv, list)
            or not deploy_argv
            or len(deploy_argv) > 128
            or any(not isinstance(item, str) or not item or len(item) > 8192 for item in deploy_argv)
            or not Path(deploy_argv[0]).is_absolute()
        ):
            raise SystemExit("activity deploy command must be bounded absolute JSON argv")
    if project_number is not None and not 1 <= project_number <= 10000:
        raise SystemExit("project number is outside the supported bound")
    if project_owner_kind not in {"user", "organization"}:
        raise SystemExit("project owner kind is invalid")
    webhook_secret_file = webhook_secret_file.expanduser()
    if webhook_secret_file.exists():
        webhook_secret = _read_secret(webhook_secret_file, "webhook secret")
    else:
        webhook_secret_file.parent.mkdir(parents=True, exist_ok=True)
        webhook_secret = secrets.token_hex(32)
        descriptor = os.open(
            webhook_secret_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(webhook_secret + "\n")
    if not re.fullmatch(r"[0-9a-f]{64}", webhook_secret):
        raise SystemExit("webhook secret must be 32 bytes encoded as lowercase hex")

    environment_lines = [
        _environment_line("BARISTA_GITHUB_REPOSITORY", repository),
        _environment_line("BARISTA_GITHUB_WEBHOOK_SECRET", webhook_secret),
        _environment_line("BARISTA_GITHUB_TOKEN", github_token),
        _environment_line("BARISTA_FACTORY_APP", "github-demo-factory@0.1.0"),
        _environment_line("BARISTA_FACTORY_TRIAGE_APP", "github-issue-triage"),
        _environment_line("BARISTA_FACTORY_WORKER_APP", "github-issue-worker"),
        _environment_line("BARISTA_GITHUB_BASE_REF", "main"),
        _environment_line("BARISTA_HOST_API_ENDPOINT", "https://beta.barista.sh"),
        _environment_line("BARISTA_HOST_API_TOKEN", host_token),
    ]
    if activity_token is not None:
        environment_lines.extend(
            [
                _environment_line("BARISTA_ACTIVITY_ENDPOINT", activity_endpoint),
                _environment_line("BARISTA_ACTIVITY_TOKEN", activity_token),
                _environment_line("BARISTA_ACTIVITY_SOURCE_URL", activity_source_url),
            ]
        )
        if deploy_argv is not None:
            environment_lines.append(
                _environment_line(
                    "BARISTA_ACTIVITY_DEPLOY_COMMAND",
                    json.dumps(deploy_argv, separators=(",", ":")),
                )
            )
    if project_token is not None and project_number is not None:
        environment_lines.extend(
            [
                _environment_line("BARISTA_GITHUB_PROJECT_TOKEN", project_token),
                _environment_line("BARISTA_GITHUB_PROJECT_NUMBER", str(project_number)),
                _environment_line(
                    "BARISTA_GITHUB_PROJECT_OWNER",
                    project_owner or repository.split("/", 4)[3],
                ),
                _environment_line(
                    "BARISTA_GITHUB_PROJECT_OWNER_KIND", project_owner_kind
                ),
            ]
        )
    environment = "".join(environment_lines).encode()

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
        "set -e; install -d -o root -g root -m 0711 /etc/barista; "
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
    if response.status_code != 200 or response.json().get(
        "repository"
    ) != repository.removeprefix("https://github.com/"):
        raise SystemExit("public controller health check failed")
    print(f"controller healthy for {repository}")
    print(
        f"webhook secret retained at {webhook_secret_file}; its value was not printed"
    )


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
    parser.add_argument("--project-token-file", type=Path)
    parser.add_argument("--project-number", type=int)
    parser.add_argument("--project-owner")
    parser.add_argument("--activity-token-file", type=Path)
    parser.add_argument("--activity-endpoint", default="https://beta.barista.sh")
    parser.add_argument(
        "--activity-source-url", default="https://github-factory.beta.barista.sh"
    )
    parser.add_argument("--activity-deploy-command")
    parser.add_argument(
        "--project-owner-kind", choices=("user", "organization"), default="user"
    )
    parser.add_argument("--cp-host", default="46.225.59.43")
    parser.add_argument(
        "--ssh-key", type=Path, default=Path.home() / ".ssh/barista_hetzner"
    )
    parser.add_argument(
        "--known-hosts",
        type=Path,
        default=Path.home() / ".ssh/known_hosts.barista-deploy",
    )
    parser.add_argument(
        "--public-url", default="https://github-factory.beta.barista.sh"
    )
    args = parser.parse_args()
    provision(**vars(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
