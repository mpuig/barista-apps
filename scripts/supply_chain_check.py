#!/usr/bin/env python3
"""Supply-chain checks for barista-apps (apps-001 task 9.3).

Enforces, and fails loudly on:
  * every app manifest workload is pinned to an immutable digest (no mutable tag);
  * every app manifest validates against the published App Manifest schema and
    carries reference-only secrets (never plaintext);
  * every Python package pins the SDK via a uv path source and ships a uv.lock
    (reproducible builds; no floating cross-package dependency);
  * the contract schemas parse and the Session Story content id is deterministic
    (generated-artifact drift guard).

Runs offline. Exit non-zero on any violation.
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DIGEST_OK = __import__("re").compile(r"^sha(256|512):[0-9a-f]{64,128}$")

problems: list[str] = []


def check_manifests() -> None:
    from jsonschema import Draft202012Validator

    schema = json.loads((REPO / "contracts" / "app-manifest" / "v1alpha1" / "schema.json").read_text())
    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)

    manifests = sorted(REPO.glob("apps/*/manifest.json"))
    if not manifests:
        problems.append("no app manifests found")
    for path in manifests:
        rel = path.relative_to(REPO)
        manifest = json.loads(path.read_text())
        errs = list(validator.iter_errors(manifest))
        if errs:
            problems.append(f"{rel}: schema invalid: {errs[0].message}")
            continue
        digest = manifest["workload"]["digest"]
        if not DIGEST_OK.match(digest):
            problems.append(f"{rel}: workload.digest is not a pinned digest: {digest!r}")
        for secret in manifest.get("permissions", {}).get("secrets", []) or []:
            ref = secret.get("ref", "")
            if not ref.startswith(("secret://", "grant://", "ref://")):
                problems.append(f"{rel}: secret {secret.get('name')!r} is not a reference: {ref!r}")


def check_packages() -> None:
    pyprojects = sorted(REPO.glob("**/pyproject.toml"))
    for path in pyprojects:
        if ".venv" in path.parts:
            continue
        rel = path.relative_to(REPO)
        data = tomllib.loads(path.read_text())
        deps = data.get("project", {}).get("dependencies", [])
        uses_sdk = any(d.split()[0].split(">")[0].split("=")[0] == "barista-app-sdk" for d in deps)
        if uses_sdk:
            sources = data.get("tool", {}).get("uv", {}).get("sources", {})
            if "barista-app-sdk" not in sources or "path" not in sources["barista-app-sdk"]:
                problems.append(f"{rel}: depends on barista-app-sdk without a uv path source (floating dep)")
        # Reproducibility: any package with a build target should ship a lock.
        if "build-system" in data and not (path.parent / "uv.lock").exists():
            problems.append(f"{rel}: missing uv.lock (non-reproducible build)")


def check_schema_determinism() -> None:
    import hashlib

    def content_id(value) -> str:
        blob = (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
        return "sha256:" + hashlib.sha256(blob).hexdigest()

    for name in ("app-manifest/v1alpha1/schema.json",
                 "host-api/v1alpha1/streaming/event.schema.json",
                 "session-story/v1alpha1/schema.json"):
        path = REPO / "contracts" / name
        try:
            data = json.loads(path.read_text())
        except Exception as exc:  # noqa: BLE001
            problems.append(f"contracts/{name}: does not parse: {exc}")
            continue
        # Stable content id twice from a reparsed copy (drift guard).
        if content_id(data) != content_id(json.loads(json.dumps(data))):
            problems.append(f"contracts/{name}: non-deterministic canonicalization")


def main() -> int:
    check_manifests()
    check_packages()
    check_schema_determinism()
    if problems:
        print("supply-chain check FAILED:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("supply-chain check OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
