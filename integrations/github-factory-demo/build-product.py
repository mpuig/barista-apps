#!/usr/bin/env python3
"""Restricted node-side builder for the beta Factory product repository.

Install as a forced-command SSH target. It accepts no argv or environment
configuration: repository, branch, and loopback registry are operator-owned
constants. Request data arrives as bounded JSON on stdin.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

REPOSITORY = "https://github.com/mpuig/barista-factory-demo"
BASE_REF = "main"
REGISTRY = "127.0.0.1:5000/barista-products"
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def _run(command: list[str], *, cwd: Path | None = None, timeout: int = 1200) -> str:
    result = subprocess.run(  # noqa: S603 - executable/argv are fixed below
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        timeout=timeout,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"},
    )
    if len(result.stdout) > 64 * 1024 or len(result.stderr) > 64 * 1024:
        raise ValueError("builder output exceeded 64 KiB")
    if result.returncode != 0:
        error = " ".join(result.stderr.decode(errors="replace").split())[:1000]
        raise RuntimeError(f"builder tool failed ({result.returncode}): {error}")
    return result.stdout.decode().strip()


def _request() -> dict:
    raw = os.read(0, 64 * 1024 + 1)
    if len(raw) > 64 * 1024:
        raise ValueError("builder request exceeded 64 KiB")
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get("repository") != REPOSITORY:
        raise ValueError("builder repository is outside the fixed allowlist")
    if not isinstance(value.get("operation_id"), str) or not value["operation_id"].startswith("ar-"):
        raise ValueError("builder operation identity is invalid")
    if not isinstance(value.get("program_id"), str) or _ID.fullmatch(value["program_id"]) is None:
        raise ValueError("builder program identity is invalid")
    if not isinstance(value.get("accepted_commit"), str) or _COMMIT.fullmatch(value["accepted_commit"]) is None:
        raise ValueError("builder commit identity is invalid")
    acceptance = value.get("acceptance")
    if (
        not isinstance(acceptance, dict)
        or acceptance.get("accepted") is not True
        or acceptance.get("exit_code") != 0
        or acceptance.get("assembled_commit") != value["accepted_commit"]
    ):
        raise ValueError("builder requires an independently accepted exact commit")
    return value


def _refuse_lfs(workspace: Path) -> None:
    marker = b"version https://git-lfs.github.com/spec/v1"
    count = 0
    for path in workspace.rglob("*"):
        if ".git" in path.parts or not path.is_file():
            continue
        count += 1
        if count > 100_000:
            raise ValueError("builder source exceeds the file-count bound")
        with path.open("rb") as stream:
            if stream.read(len(marker)) == marker:
                raise ValueError("git LFS is not supported")


def main() -> int:
    request = _request()
    suffix = request["accepted_commit"][:12]
    image = f"{REGISTRY}/product-{request['program_id']}:{suffix}"
    with tempfile.TemporaryDirectory(prefix="barista-product-") as temporary:
        workspace = Path(temporary) / "source"
        _run(
            [
                "git",
                "clone",
                "--no-checkout",
                "--filter=blob:none",
                "--branch",
                BASE_REF,
                REPOSITORY,
                str(workspace),
            ],
            timeout=300,
        )
        _run(["git", "checkout", "--detach", f"origin/{BASE_REF}"], cwd=workspace)
        if _run(["git", "rev-parse", "HEAD"], cwd=workspace) != request["accepted_commit"]:
            raise ValueError("trusted main no longer matches the accepted commit")
        if (workspace / ".gitmodules").exists():
            raise ValueError("git submodules are not supported")
        _refuse_lfs(workspace)
        _run(["docker", "build", "--pull", "--tag", image, "."], cwd=workspace)
        _run(["docker", "push", image], timeout=600)
        repo_digests = json.loads(
            _run(["docker", "image", "inspect", image, "--format={{json .RepoDigests}}"])
        )
    pinned = next((item for item in repo_digests if "@sha256:" in item), None)
    digest = pinned.split("@", 1)[1] if isinstance(pinned, str) else ""
    if _DIGEST.fullmatch(digest) is None:
        raise ValueError("registry did not report a valid digest")
    print(json.dumps({"image": image, "digest": digest}, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
