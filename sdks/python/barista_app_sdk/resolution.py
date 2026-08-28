"""Resolve an App Run selector to a validated, immutable app identity."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Optional
from urllib.parse import unquote, urlparse

from jsonschema import Draft202012Validator

from .errors import InvalidRequestError, TerminalError
from .models import InstalledApp
from .runs import content_id


def _invalid(message: str, *, code: str = "app_source.invalid", details: Optional[dict] = None):
    return InvalidRequestError(
        message, code=code, details=details or {}, error_class="invalid_request"
    )


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(k): _freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: _thaw(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_thaw(v) for v in value]
    return value


def _manifest_schema() -> dict:
    resource = files("barista_app_sdk").joinpath(
        "_contracts/app-manifest-v1alpha1.schema.json"
    )
    return json.loads(resource.read_text(encoding="utf-8"))


def validate_manifest(manifest: Mapping[str, Any]) -> None:
    errors = sorted(
        Draft202012Validator(
            _manifest_schema(), format_checker=Draft202012Validator.FORMAT_CHECKER
        ).iter_errors(_thaw(manifest)),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        first = errors[0]
        location = "/".join(str(part) for part in first.absolute_path) or "<root>"
        raise _invalid(
            f"app manifest is invalid at {location}: {first.message}",
            details={"path": list(first.absolute_path), "validator": first.validator},
        )


@dataclass(frozen=True)
class ResolvedApp:
    """The identities selected before any install or session mutation."""

    name: str
    version: str
    workload_digest: str
    manifest_digest: str
    manifest: Mapping[str, Any]
    source: str
    source_revision: str
    installed: bool = False

    @property
    def reference(self) -> str:
        return f"{self.name}@{self.version}"

    def manifest_document(self) -> dict:
        return _thaw(self.manifest)

    @classmethod
    def from_installed(cls, app: InstalledApp) -> "ResolvedApp":
        validate_manifest(app.manifest)
        return cls(
            name=app.name,
            version=app.version,
            workload_digest=app.digest,
            manifest_digest=content_id(app.manifest),
            manifest=_freeze(app.manifest),
            source=f"installed://{app.name}",
            # The canonical manifest digest is the immutable revision of the
            # provider-held declaration even though no source repository exists.
            source_revision=content_id(app.manifest),
            installed=True,
        )


def _run_git(path: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=check,
        capture_output=True,
        text=True,
    )


def _local_source_identity(manifest_path: Path, *, allow_dirty: bool) -> tuple[str, str]:
    probe = _run_git(manifest_path.parent, "rev-parse", "--show-toplevel", check=False)
    if probe.returncode != 0:
        # A non-Git local manifest is still immutable for this run by its
        # canonical content id. It is explicitly a file source, not a repository
        # revision claim.
        manifest = json.loads(manifest_path.read_text())
        digest = content_id(manifest)
        return manifest_path.as_uri(), digest

    root = Path(probe.stdout.strip()).resolve()
    try:
        relative = manifest_path.relative_to(root)
    except ValueError as exc:  # pragma: no cover - rev-parse cannot produce this honestly
        raise _invalid("resolved Git root does not contain the app manifest") from exc

    tracked = _run_git(root, "ls-files", "--error-unmatch", str(relative), check=False)
    if tracked.returncode != 0:
        raise _invalid(f"app manifest is not tracked by its Git source: {relative}")
    dirty = _run_git(root, "status", "--porcelain", "--untracked-files=all").stdout.strip()
    if dirty and not allow_dirty:
        raise _invalid(
            "app source repository is dirty; commit it or use explicit development mode",
            code="app_source.dirty",
            details={"status": dirty.splitlines()},
        )
    revision = _run_git(root, "rev-parse", "HEAD").stdout.strip()
    if not revision:
        raise _invalid("app source repository has no commit")
    source = "git+" + root.as_uri() + "#" + str(relative)
    return source, revision if not dirty else f"{revision}+dirty:{content_id(json.loads(manifest_path.read_text()))}"


def resolve_local_app(source: str | Path, *, allow_dirty: bool = False) -> ResolvedApp:
    """Resolve a local manifest file or app directory before provider mutation.

    A clean Git source records HEAD. A non-Git file records its canonical
    manifest digest. Dirty Git is refused unless the caller explicitly selected
    development mode, in which case the revision records both HEAD and content.
    """
    if isinstance(source, str) and source.startswith("file://"):
        parsed = urlparse(source)
        path = Path(unquote(parsed.path))
    else:
        path = Path(source)
    path = path.expanduser().resolve()
    manifest_path = path / "manifest.json" if path.is_dir() else path
    if not manifest_path.is_file():
        raise _invalid(f"app manifest not found: {manifest_path}", code="app_source.not_found")
    try:
        document = json.loads(manifest_path.read_text())
    except json.JSONDecodeError as exc:
        raise _invalid(f"app manifest is not valid JSON: {exc}") from exc
    validate_manifest(document)
    source_uri, revision = _local_source_identity(manifest_path, allow_dirty=allow_dirty)
    return ResolvedApp(
        name=document["name"],
        version=document["version"],
        workload_digest=document["workload"]["digest"],
        manifest_digest=content_id(document),
        manifest=_freeze(document),
        source=source_uri,
        source_revision=revision,
        installed=False,
    )


def split_installed_selector(selector: str) -> tuple[str, Optional[str]]:
    """`name` or `name@version`; URLs/paths are resolved elsewhere."""
    if "@" not in selector:
        return selector, None
    name, version = selector.rsplit("@", 1)
    if not name or not version:
        raise _invalid(f"invalid installed app selector: {selector!r}")
    return name, version


def resolve_installed_app(client, selector: str) -> ResolvedApp:
    name, expected_version = split_installed_selector(selector)
    try:
        installed = client.get_installed_app(name)
    except TerminalError as exc:
        raise _invalid(
            f"installed app {name!r} was not found",
            code="app_source.not_found",
            details={"app": name},
        ) from exc
    if expected_version is not None and installed.version != expected_version:
        raise _invalid(
            f"installed app {name!r} is version {installed.version}, not {expected_version}",
            code="app_source.version_mismatch",
            details={"expected": expected_version, "actual": installed.version},
        )
    return ResolvedApp.from_installed(installed)


def resolve_app(client, selector: str, *, allow_dirty: bool = False) -> ResolvedApp:
    """Resolve an installed selector or an explicit local path/file URI."""
    if selector.startswith("file://") or Path(selector).expanduser().exists():
        return resolve_local_app(selector, allow_dirty=allow_dirty)
    if "://" in selector:
        raise _invalid(
            "remote app source resolution is not implemented; install a pinned manifest or use a local source",
            code="app_source.unsupported",
        )
    return resolve_installed_app(client, selector)
