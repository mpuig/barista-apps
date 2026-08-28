"""Portable source-binding resolution and bounded local materialization.

Bindings are untrusted references, not authority.  Resolution selects an
immutable identity once; materialization checks out that identity and never
silently follows a moving branch to a different commit.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping, Optional
from urllib.parse import unquote, urlparse

from .errors import InvalidRequestError, TerminalError
from .runs import RunBinding

GIT_REPOSITORY_KIND = "sh.barista.git.repository"
LOCAL_TEXT_KINDS = frozenset({"sh.barista.text", "sh.barista.specification"})
_DEFAULT_REF = "HEAD"
_COMMIT = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")


def _invalid(message: str, *, code: str, details: Optional[dict] = None) -> InvalidRequestError:
    return InvalidRequestError(
        message, code=code, details=details or {}, error_class="invalid_request"
    )


def _terminal(message: str, *, code: str, details: Optional[dict] = None) -> TerminalError:
    return TerminalError(message, code=code, details=details or {}, error_class="terminal")


@dataclass(frozen=True)
class ResolvedGitRepository:
    uri: str
    requested_ref: str
    commit: str
    workspace: Path
    size_bytes: int
    submodules: str
    lfs: str

    def to_result_binding(self) -> dict:
        return {
            "kind": GIT_REPOSITORY_KIND,
            "uri": self.uri,
            "requested_ref": self.requested_ref,
            "resolved_identity": self.commit,
            "metadata": {
                "size_bytes": self.size_bytes,
                "submodules": self.submodules,
                "lfs": self.lfs,
            },
        }


@dataclass(frozen=True)
class ResolvedObjective:
    kind: str
    uri: str
    content: bytes
    digest: str
    media_type: str

    def to_result_binding(self) -> dict:
        return {
            "kind": self.kind,
            "uri": self.uri,
            "resolved_identity": self.digest,
            "metadata": {
                "size_bytes": len(self.content),
                "media_type": self.media_type,
            },
        }


def _local_path(uri: str) -> Path | None:
    parsed = urlparse(uri)
    if parsed.scheme == "file":
        if parsed.netloc not in ("", "localhost"):
            raise _invalid(
                "file binding authority must be empty or localhost",
                code="binding.file_authority",
            )
        return Path(unquote(parsed.path)).expanduser().resolve()
    if not parsed.scheme:
        return Path(uri).expanduser().resolve()
    return None


def _git_command(
    args: list[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float = 120.0,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    process_env = os.environ.copy()
    process_env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.hooksPath",
            "GIT_CONFIG_VALUE_0": os.devnull,
        }
    )
    if env:
        process_env.update(env)
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=process_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _terminal("Git source operation failed", code="binding.git_unavailable") from exc
    if check and completed.returncode != 0:
        # Git stderr can contain credential-bearing URLs supplied by external
        # helpers. Keep it out of durable errors and logs.
        raise _terminal(
            f"Git source operation exited {completed.returncode}",
            code="binding.git_failed",
        )
    return completed


@contextmanager
def _credential_environment(uri: str, credential_value: str | None) -> Iterator[dict[str, str]]:
    if credential_value is None:
        yield {}
        return
    scheme = urlparse(uri).scheme.lower()
    if scheme not in {"http", "https"}:
        raise _invalid(
            "credential materialization currently supports only HTTP(S) Git sources",
            code="binding.git_credential_scheme",
        )
    if not credential_value:
        raise _invalid("resolved Git credential is empty", code="binding.git_credential_empty")
    with tempfile.TemporaryDirectory(prefix="barista-git-askpass-") as directory:
        script = Path(directory) / "askpass.sh"
        script.write_text(
            "#!/bin/sh\ncase \"$1\" in\n"
            "  *sername*) printf '%s\\n' \"${BARISTA_GIT_USERNAME:-x-access-token}\" ;;\n"
            "  *) printf '%s\\n' \"$BARISTA_GIT_PASSWORD\" ;;\n"
            "esac\n"
        )
        script.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        yield {
            "GIT_ASKPASS": str(script),
            "GIT_ASKPASS_REQUIRE": "force",
            "BARISTA_GIT_PASSWORD": credential_value,
        }


def resolve_git_commit(
    binding: RunBinding,
    *,
    credential_value: str | None = None,
    timeout: float = 120.0,
) -> str:
    """Resolve a repository ref once without embedding credentials in argv."""
    if binding.kind != GIT_REPOSITORY_KIND:
        raise _invalid(
            f"expected {GIT_REPOSITORY_KIND}, got {binding.kind}",
            code="binding.kind_mismatch",
        )
    if binding.uri.startswith("-"):
        raise _invalid("Git repository URI cannot begin with '-'", code="binding.git_uri")
    requested = binding.ref or _DEFAULT_REF
    if requested.startswith("-") or any(char in requested for char in ("\x00", "\n", "\r")):
        raise _invalid("Git ref is not safe to resolve", code="binding.git_ref")

    local = _local_path(binding.uri)
    if local is not None:
        if credential_value is not None:
            raise _invalid(
                "local Git bindings do not accept credentials",
                code="binding.git_local_credential",
            )
        completed = _git_command(
            ["-C", str(local), "rev-parse", "--verify", "--end-of-options", f"{requested}^{{commit}}"],
            timeout=timeout,
            check=False,
        )
        commit = completed.stdout.decode("ascii", "replace").strip()
        if completed.returncode != 0 or not _COMMIT.fullmatch(commit):
            raise _terminal(
                f"Git ref {requested!r} did not resolve to one commit",
                code="binding.git_ref_not_found",
            )
        return commit

    parsed = urlparse(binding.uri)
    if parsed.scheme not in {"http", "https", "ssh", "git"}:
        raise _invalid(
            f"unsupported Git URI scheme {parsed.scheme!r}",
            code="binding.git_uri_scheme",
        )
    patterns = [requested]
    if requested.startswith("refs/tags/") and not requested.endswith("^{}"):
        patterns = [requested, f"{requested}^{{}}"]
    elif not requested.startswith("refs/") and requested != "HEAD":
        patterns = [f"refs/heads/{requested}", f"refs/tags/{requested}", f"refs/tags/{requested}^{{}}"]
    with _credential_environment(binding.uri, credential_value) as credential_env:
        completed = _git_command(
            ["ls-remote", "--exit-code", binding.uri, *patterns],
            env=credential_env,
            timeout=timeout,
            check=False,
        )
    if completed.returncode != 0:
        raise _terminal(
            f"Git ref {requested!r} did not resolve",
            code="binding.git_ref_not_found",
        )
    rows = []
    for line in completed.stdout.decode("ascii", "replace").splitlines():
        commit, separator, refname = line.partition("\t")
        if separator and _COMMIT.fullmatch(commit):
            rows.append((commit, refname))
    # An annotated tag's peeled commit is authoritative over its tag object.
    peeled = {commit for commit, refname in rows if refname.endswith("^{}")}
    heads = {commit for commit, refname in rows if refname.startswith("refs/heads/")}
    direct_tags = {commit for commit, refname in rows if refname.startswith("refs/tags/") and not refname.endswith("^{}")}
    candidates = peeled | heads | (direct_tags if not peeled else set())
    if requested == "HEAD":
        candidates = {commit for commit, refname in rows if refname == "HEAD"}
    if len(candidates) != 1:
        raise _terminal(
            f"Git ref {requested!r} is missing or ambiguous",
            code="binding.git_ref_ambiguous",
            details={"candidate_count": len(candidates)},
        )
    return next(iter(candidates))


def materialize_git_repository(
    binding: RunBinding,
    destination: str | Path,
    *,
    max_bytes: int,
    credential_value: str | None = None,
    timeout: float = 300.0,
) -> ResolvedGitRepository:
    """Resolve once, clone, and detach at the exact selected commit.

    `max_bytes` is supplied by the caller, normally from provider limits. The
    checkout is measured without following symlinks. A violation removes only
    the new destination this function created.
    """
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    target = Path(destination).expanduser().resolve()
    if target.exists():
        raise _invalid(
            f"Git destination already exists: {target}",
            code="binding.git_destination_exists",
        )
    commit = resolve_git_commit(binding, credential_value=credential_value, timeout=timeout)
    submodules = str(binding.options.get("submodules", "reject"))
    lfs = str(binding.options.get("lfs", "reject"))
    if submodules not in {"reject", "ignore"}:
        raise _invalid(
            "binding.options.submodules must be 'reject' or 'ignore'",
            code="binding.git_submodules_option",
        )
    if lfs not in {"reject", "ignore"}:
        raise _invalid(
            "binding.options.lfs must be 'reject' or 'ignore'",
            code="binding.git_lfs_option",
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with _credential_environment(binding.uri, credential_value) as credential_env:
            _git_command(
                ["clone", "--no-checkout", "--filter=blob:none", "--", binding.uri, str(target)],
                env=credential_env,
                timeout=timeout,
            )
        created = True
        exists = _git_command(
            ["-C", str(target), "cat-file", "-e", f"{commit}^{{commit}}"],
            timeout=timeout,
            check=False,
        )
        if exists.returncode != 0:
            # The requested ref moved between resolution and acquisition. Never
            # replace the selected commit with its new target.
            raise _terminal(
                "resolved Git commit became unavailable during materialization",
                code="binding.git_moving_ref",
                details={"resolved_commit": commit},
            )
        _git_command(
            ["-C", str(target), "-c", f"core.hooksPath={os.devnull}", "checkout", "--detach", commit],
            timeout=timeout,
        )

        has_submodules = (target / ".gitmodules").is_file()
        if has_submodules and submodules == "reject":
            raise _terminal(
                "repository declares submodules; choose explicit ignore behavior",
                code="binding.git_submodules_refused",
            )
        lfs_probe = _git_command(
            ["-C", str(target), "grep", "-I", "-l", "filter=lfs", commit, "--", "*.gitattributes", ".gitattributes"],
            timeout=timeout,
            check=False,
        )
        has_lfs = lfs_probe.returncode == 0 and bool(lfs_probe.stdout.strip())
        if has_lfs and lfs == "reject":
            raise _terminal(
                "repository declares Git LFS content; choose explicit ignore behavior",
                code="binding.git_lfs_refused",
            )

        size = _tree_size(target, max_bytes=max_bytes)
        return ResolvedGitRepository(
            uri=binding.uri,
            requested_ref=binding.ref or _DEFAULT_REF,
            commit=commit,
            workspace=target,
            size_bytes=size,
            submodules="ignored" if has_submodules else "none",
            lfs="pointer-files" if has_lfs else "none",
        )
    except Exception:
        if created:
            shutil.rmtree(target, ignore_errors=True)
        raise


def _tree_size(root: Path, *, max_bytes: int) -> int:
    total = 0
    for directory, names, files in os.walk(root, followlinks=False):
        for name in [*names, *files]:
            path = Path(directory) / name
            try:
                total += path.lstat().st_size
            except FileNotFoundError:
                continue
            if total > max_bytes:
                raise _terminal(
                    f"materialized Git repository exceeds the {max_bytes}-byte limit",
                    code="binding.git_size_limit",
                    details={"max_bytes": max_bytes},
                )
    return total


def resolve_local_objective(binding: RunBinding, *, max_bytes: int) -> ResolvedObjective:
    """Read bounded local objective text without granting it policy authority."""
    if binding.kind not in LOCAL_TEXT_KINDS:
        raise _invalid(
            f"unsupported local objective kind {binding.kind!r}",
            code="binding.objective_kind",
        )
    if binding.credential is not None:
        raise _invalid(
            "local objective bindings do not accept credentials",
            code="binding.objective_credential",
        )
    path = _local_path(binding.uri)
    if path is None or not path.is_file():
        raise _terminal("local objective file was not found", code="binding.objective_not_found")
    size = path.stat().st_size
    if size > max_bytes:
        raise _terminal(
            f"objective exceeds the {max_bytes}-byte limit",
            code="binding.objective_size_limit",
            details={"max_bytes": max_bytes, "size_bytes": size},
        )
    content = path.read_bytes()
    if len(content) > max_bytes:
        raise _terminal("objective grew while being read", code="binding.objective_moving")
    try:
        content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _invalid("objective must be UTF-8 text", code="binding.objective_encoding") from exc
    digest = "sha256:" + hashlib.sha256(content).hexdigest()
    media_type = str(binding.options.get("media_type", "text/plain; charset=utf-8"))
    return ResolvedObjective(
        kind=binding.kind,
        uri=binding.uri,
        content=content,
        digest=digest,
        media_type=media_type,
    )
