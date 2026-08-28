"""Offline-testable forge objectives and explicit draft-change delivery."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Protocol, runtime_checkable

from .errors import InvalidRequestError, TerminalError
from .runs import DeliveryRequest, RunBinding
from .sensitive import assert_no_high_confidence_secrets
from .sources import ResolvedGitRepository

DRAFT_PULL_REQUEST_KIND = "com.github.draft-pull-request"
GITHUB_ISSUE_KIND = "com.github.issue"
_BRANCH = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._/-]*[A-Za-z0-9])?$")


def _invalid(message: str, *, code: str, details: dict | None = None) -> InvalidRequestError:
    return InvalidRequestError(
        message, code=code, details=details or {}, error_class="invalid_request"
    )


def _refused(message: str, *, code: str, details: dict | None = None) -> TerminalError:
    return TerminalError(message, code=code, details=details or {}, error_class="terminal")


@dataclass(frozen=True)
class ForgeIssue:
    kind: str
    uri: str
    repository_uri: str
    number: int
    title: str
    body: str
    revision: str
    state: str = "open"

    def objective(self) -> dict:
        """Untrusted objective content; deliberately contains no policy fields."""
        return {
            "kind": self.kind,
            "uri": self.uri,
            "resolved_identity": self.revision,
            "content": {
                "number": self.number,
                "title": self.title,
                "body": self.body,
                "state": self.state,
            },
        }

    def to_result_binding(self) -> dict:
        return {
            "kind": self.kind,
            "uri": self.uri,
            "resolved_identity": self.revision,
            "metadata": {
                "repository_uri": self.repository_uri,
                "number": self.number,
                "state": self.state,
            },
        }


@dataclass(frozen=True)
class DraftChange:
    url: str
    repository_uri: str
    number: int
    base_commit: str
    head_branch: str
    title: str
    body: str
    patch_digest: str
    draft: bool = True

    def to_result_output(self) -> dict:
        return {
            "kind": DRAFT_PULL_REQUEST_KIND,
            "uri": self.url,
            "metadata": {
                "repository_uri": self.repository_uri,
                "number": self.number,
                "base_commit": self.base_commit,
                "head_branch": self.head_branch,
                "draft": self.draft,
                "patch_digest": self.patch_digest,
            },
        }


@dataclass(frozen=True)
class PatchArtifact:
    data: bytes
    digest: str
    size_bytes: int
    path: Path | None = None

    def to_result_output(self) -> dict:
        output = {
            "kind": "sh.barista.git.patch",
            "digest": self.digest,
            "media_type": "application/vnd.git.patch",
            "metadata": {"size_bytes": self.size_bytes},
        }
        if self.path is not None:
            output["uri"] = self.path.as_uri()
        return output


@dataclass(frozen=True)
class BranchOutput:
    branch: str
    commit: str
    base_commit: str

    def to_result_output(self) -> dict:
        return {
            "kind": "sh.barista.git.branch",
            "uri": f"git-ref://{self.branch}",
            "metadata": {"commit": self.commit, "base_commit": self.base_commit},
        }


@runtime_checkable
class ForgeAdapter(Protocol):
    issue_kind: str
    delivery_kind: str

    def resolve_issue(self, uri: str) -> ForgeIssue: ...

    def resolve_ref(self, repository_uri: str, ref: str) -> str: ...

    def create_draft_change(
        self,
        *,
        repository_uri: str,
        base_commit: str,
        head_branch: str,
        title: str,
        body: str,
        patch: PatchArtifact,
    ) -> DraftChange: ...


def resolve_issue_objective(
    binding: RunBinding,
    adapter: ForgeAdapter,
    *,
    max_bytes: int = 1024 * 1024,
) -> ForgeIssue:
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    if binding.kind != adapter.issue_kind:
        raise _invalid(
            f"forge adapter does not support objective kind {binding.kind!r}",
            code="binding.issue_kind",
        )
    issue = adapter.resolve_issue(binding.uri)
    if issue.uri != binding.uri:
        raise _refused(
            "forge adapter returned an issue for a different URI",
            code="binding.issue_identity",
        )
    size = len(issue.title.encode("utf-8")) + len(issue.body.encode("utf-8"))
    if size > max_bytes:
        raise _refused(
            f"forge objective exceeds the {max_bytes}-byte limit",
            code="binding.issue_size_limit",
            details={"max_bytes": max_bytes, "size_bytes": size},
        )
    return issue


def create_workspace_patch(
    workspace: str | Path,
    *,
    output: str | Path | None = None,
    max_bytes: int = 16 * 1024 * 1024,
) -> PatchArtifact:
    """Create a binary-capable patch, including new and deleted files.

    Staging is used only as a deterministic diff mechanism and is reset before
    return; working-tree bytes are not changed. Git hooks are disabled.
    """
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    root = Path(workspace).expanduser().resolve()
    env = os.environ.copy()
    env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.hooksPath",
            "GIT_CONFIG_VALUE_0": os.devnull,
        }
    )

    def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
        try:
            process = subprocess.run(
                ["git", "-C", str(root), *args],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise _refused("could not inspect Git workspace", code="output.patch_unavailable") from exc
        if check and process.returncode != 0:
            raise _refused(
                f"Git patch operation exited {process.returncode}",
                code="output.patch_failed",
            )
        return process

    git("rev-parse", "--is-inside-work-tree")
    try:
        git("add", "-A")
        patch = git("diff", "--cached", "--binary", "--no-ext-diff", "--full-index", "--").stdout
    finally:
        # Reset only the index. The app's working tree and untracked files remain.
        git("reset", "--mixed", "--quiet", check=False)
    if len(patch) > max_bytes:
        raise _refused(
            f"patch exceeds the {max_bytes}-byte limit",
            code="output.patch_size_limit",
            details={"max_bytes": max_bytes, "size_bytes": len(patch)},
        )
    assert_no_high_confidence_secrets(patch.decode("utf-8", "replace"))
    digest = "sha256:" + hashlib.sha256(patch).hexdigest()
    path = Path(output).expanduser().resolve() if output is not None else None
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
        try:
            temporary.write_bytes(patch)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return PatchArtifact(data=patch, digest=digest, size_bytes=len(patch), path=path)


def commit_workspace_branch(
    workspace: str | Path,
    *,
    base_commit: str,
    branch: str,
    message: str,
) -> BranchOutput:
    """Create an explicit local branch output from a verified workspace."""
    root = Path(workspace).expanduser().resolve()
    if not branch or not _BRANCH.fullmatch(branch) or branch.startswith("-") or ".." in branch:
        raise _invalid("branch output requires a safe branch name", code="output.branch")
    assert_no_high_confidence_secrets(message)
    env = os.environ.copy()
    env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.hooksPath",
            "GIT_CONFIG_VALUE_0": os.devnull,
            "GIT_AUTHOR_NAME": "Barista App",
            "GIT_AUTHOR_EMAIL": "app@barista.invalid",
            "GIT_COMMITTER_NAME": "Barista App",
            "GIT_COMMITTER_EMAIL": "app@barista.invalid",
        }
    )

    def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
        process = subprocess.run(
            ["git", "-C", str(root), *args],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
            check=False,
        )
        if check and process.returncode != 0:
            raise _refused(
                f"Git branch operation exited {process.returncode}",
                code="output.branch_failed",
            )
        return process

    head = git("rev-parse", "HEAD").stdout.decode("ascii", "replace").strip()
    if head != base_commit:
        raise _refused(
            "workspace HEAD no longer matches its resolved base",
            code="output.branch_moving_base",
            details={"resolved_commit": base_commit, "current_commit": head},
        )
    if git("show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False).returncode == 0:
        raise _refused("local branch already exists", code="output.branch_exists")
    patch = create_workspace_patch(root)
    if patch.size_bytes == 0:
        raise _refused("workspace has no changes to commit", code="output.branch_empty")
    git("switch", "-c", branch)
    git("add", "-A")
    git("commit", "--no-verify", "-m", message)
    commit = git("rev-parse", "HEAD").stdout.decode("ascii", "replace").strip()
    return BranchOutput(branch=branch, commit=commit, base_commit=base_commit)


def deliver_draft_change(
    delivery: DeliveryRequest,
    *,
    adapter: ForgeAdapter,
    repository: ResolvedGitRepository,
    run_state: str,
    patch: PatchArtifact,
    title: str,
    body: str,
) -> DraftChange:
    """Perform the declared side effect only after all independent checks pass."""
    if delivery.kind != adapter.delivery_kind:
        raise _invalid(
            f"forge adapter does not support delivery kind {delivery.kind!r}",
            code="delivery.kind",
        )
    if run_state != "succeeded":
        raise _refused(
            "draft change delivery requires a verified succeeded result",
            code="delivery.verification_required",
            details={"run_state": run_state},
        )
    if delivery.target != repository.uri:
        raise _refused(
            "delivery target is outside the bound repository",
            code="delivery.repository_scope",
            details={"bound_repository": repository.uri},
        )
    base_ref = str(delivery.options.get("base_ref", repository.requested_ref))
    current = adapter.resolve_ref(repository.uri, base_ref)
    if current != repository.commit:
        raise _refused(
            "repository base ref moved after run acquisition",
            code="delivery.moving_base",
            details={"resolved_commit": repository.commit, "current_commit": current},
        )
    branch = str(delivery.options.get("head_branch", ""))
    if not branch or not _BRANCH.fullmatch(branch) or branch.startswith("-") or ".." in branch:
        raise _invalid("delivery requires a safe head_branch", code="delivery.branch")
    assert_no_high_confidence_secrets({"title": title, "body": body})
    return adapter.create_draft_change(
        repository_uri=repository.uri,
        base_commit=repository.commit,
        head_branch=branch,
        title=title,
        body=body,
        patch=patch,
    )


@dataclass
class FakeForge:
    """Deterministic offline forge used by standalone acceptance and dishonest cases."""

    issue_kind: str = GITHUB_ISSUE_KIND
    delivery_kind: str = DRAFT_PULL_REQUEST_KIND
    repositories: dict[str, dict[str, str]] = field(default_factory=dict)
    issues: dict[str, ForgeIssue] = field(default_factory=dict)
    changes: list[DraftChange] = field(default_factory=list)

    def add_repository(self, uri: str, *, refs: Mapping[str, str]) -> None:
        self.repositories[uri] = dict(refs)

    def move_ref(self, uri: str, ref: str, commit: str) -> None:
        if uri not in self.repositories:
            raise KeyError(uri)
        self.repositories[uri][ref] = commit

    def add_issue(
        self,
        uri: str,
        *,
        repository_uri: str,
        number: int,
        title: str,
        body: str,
        state: str = "open",
        revision: str | None = None,
    ) -> ForgeIssue:
        if repository_uri not in self.repositories:
            raise ValueError("issue repository is not registered")
        resolved_revision = revision or "sha256:" + hashlib.sha256(
            f"{number}\0{title}\0{body}\0{state}".encode()
        ).hexdigest()
        issue = ForgeIssue(
            kind=self.issue_kind,
            uri=uri,
            repository_uri=repository_uri,
            number=number,
            title=title,
            body=body,
            revision=resolved_revision,
            state=state,
        )
        self.issues[uri] = issue
        return issue

    def resolve_issue(self, uri: str) -> ForgeIssue:
        try:
            return self.issues[uri]
        except KeyError as exc:
            raise _refused("forge issue was not found", code="binding.issue_not_found") from exc

    def resolve_ref(self, repository_uri: str, ref: str) -> str:
        try:
            return self.repositories[repository_uri][ref]
        except KeyError as exc:
            raise _refused("forge repository ref was not found", code="delivery.base_not_found") from exc

    def create_draft_change(
        self,
        *,
        repository_uri: str,
        base_commit: str,
        head_branch: str,
        title: str,
        body: str,
        patch: PatchArtifact,
    ) -> DraftChange:
        if repository_uri not in self.repositories:
            raise _refused("forge repository was not found", code="delivery.repository_not_found")
        # Idempotent by repository + head branch: retry returns the existing
        # draft instead of creating a duplicate pull request.
        for change in self.changes:
            if change.repository_uri == repository_uri and change.head_branch == head_branch:
                if change.patch_digest != patch.digest or change.base_commit != base_commit:
                    raise _refused(
                        "draft branch already exists with different content",
                        code="delivery.branch_conflict",
                    )
                return change
        number = len([c for c in self.changes if c.repository_uri == repository_uri]) + 1
        change = DraftChange(
            url=f"fake://pull/{number}",
            repository_uri=repository_uri,
            number=number,
            base_commit=base_commit,
            head_branch=head_branch,
            title=title,
            body=body,
            patch_digest=patch.digest,
        )
        self.changes.append(change)
        return change
