"""Offline-testable forge objectives and explicit draft-change delivery."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Protocol, runtime_checkable
from urllib.parse import quote, urlparse

import httpx

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
    base_ref: str
    head_branch: str
    head_commit: str
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
                "base_ref": self.base_ref,
                "head_branch": self.head_branch,
                "head_commit": self.head_commit,
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
        base_ref: str,
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
    marker = f"<!-- barista-patch-digest:{patch.digest} -->"
    delivery_body = body if marker in body else body + "\n\n" + marker
    return adapter.create_draft_change(
        repository_uri=repository.uri,
        base_commit=repository.commit,
        base_ref=base_ref,
        head_branch=branch,
        title=title,
        body=delivery_body,
        patch=patch,
    )


class GitHubForge:
    """GitHub issue and draft-PR adapter with token-safe Git publication."""

    issue_kind = GITHUB_ISSUE_KIND
    delivery_kind = DRAFT_PULL_REQUEST_KIND

    def __init__(self, *, token: str | None = None, api_url: str = "https://api.github.com"):
        self._token = token
        self._api_url = api_url.rstrip("/")

    @staticmethod
    def _repository(uri: str) -> tuple[str, str]:
        parsed = urlparse(uri)
        parts = [part for part in parsed.path.split("/") if part]
        if parsed.scheme != "https" or parsed.hostname != "github.com" or len(parts) != 2:
            raise _invalid("GitHub adapter requires an https://github.com/OWNER/REPO repository", code="forge.repository")
        owner, repository = parts
        repository = repository[:-4] if repository.endswith(".git") else repository
        if not owner or not repository:
            raise _invalid("GitHub repository URI is incomplete", code="forge.repository")
        return owner, repository

    def _request(
        self,
        method: str,
        path: str,
        *,
        document: dict | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> dict | list:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "barista-app-sdk",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            response = httpx.request(
                method,
                self._api_url + path,
                headers=headers,
                json=document,
                follow_redirects=False,
                timeout=30.0,
            )
        except httpx.HTTPError as exc:
            raise _refused("GitHub request failed", code="forge.transport") from exc
        if response.status_code not in expected:
            raise _refused(
                f"GitHub request was refused with HTTP {response.status_code}",
                code="forge.http",
                details={"status": response.status_code},
            )
        try:
            return response.json()
        except ValueError as exc:
            raise _refused("GitHub returned invalid JSON", code="forge.response") from exc

    def resolve_issue(self, uri: str) -> ForgeIssue:
        parsed = urlparse(uri)
        parts = [part for part in parsed.path.split("/") if part]
        if (
            parsed.scheme != "https"
            or parsed.hostname != "github.com"
            or len(parts) != 4
            or parts[2] != "issues"
        ):
            raise _invalid("GitHub issue URI is invalid", code="binding.issue_uri")
        try:
            number = int(parts[3])
        except ValueError as exc:
            raise _invalid("GitHub issue number is invalid", code="binding.issue_uri") from exc
        owner, repository = parts[0], parts[1]
        document = self._request("GET", f"/repos/{quote(owner)}/{quote(repository)}/issues/{number}")
        if not isinstance(document, dict) or "pull_request" in document:
            raise _refused("GitHub objective is not an issue", code="binding.issue_identity")
        title = str(document.get("title", ""))
        body = str(document.get("body") or "")
        state = str(document.get("state", "open"))
        revision_document = {
            "number": number,
            "title": title,
            "body": body,
            "state": state,
            "updated_at": document.get("updated_at"),
        }
        revision = "sha256:" + hashlib.sha256(
            json.dumps(revision_document, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return ForgeIssue(
            kind=self.issue_kind,
            uri=uri,
            repository_uri=f"https://github.com/{owner}/{repository}",
            number=number,
            title=title,
            body=body,
            revision=revision,
            state=state,
        )

    def resolve_ref(self, repository_uri: str, ref: str) -> str:
        owner, repository = self._repository(repository_uri)
        encoded = quote(ref, safe="")
        document = self._request(
            "GET", f"/repos/{quote(owner)}/{quote(repository)}/git/ref/heads/{encoded}"
        )
        if not isinstance(document, dict):
            raise _refused("GitHub ref response is invalid", code="delivery.base_not_found")
        return str((document.get("object") or {}).get("sha", ""))

    def create_issue_comment(self, issue_uri: str, body: str) -> str:
        """Post a non-secret result link to one canonical GitHub issue."""
        if not self._token:
            raise _refused("GitHub issue comment requires a token", code="delivery.credential_required")
        assert_no_high_confidence_secrets(body)
        parsed = urlparse(issue_uri)
        parts = [part for part in parsed.path.split("/") if part]
        if (
            parsed.scheme != "https"
            or parsed.hostname != "github.com"
            or len(parts) != 4
            or parts[2] != "issues"
            or not parts[3].isdigit()
        ):
            raise _invalid("GitHub issue URI is invalid", code="binding.issue_uri")
        path = f"/repos/{quote(parts[0])}/{quote(parts[1])}/issues/{parts[3]}/comments"
        for page in range(1, 11):
            existing = self._request("GET", path + f"?per_page=100&page={page}")
            if not isinstance(existing, list):
                raise _refused("GitHub comment list response is invalid", code="forge.response")
            for comment in existing:
                if (
                    isinstance(comment, dict)
                    and comment.get("body") == body
                    and comment.get("html_url")
                ):
                    return str(comment["html_url"])
            if len(existing) < 100:
                break
        document = self._request(
            "POST",
            path,
            document={"body": body},
            expected=(201,),
        )
        if not isinstance(document, dict) or not document.get("html_url"):
            raise _refused("GitHub comment response is invalid", code="forge.response")
        return str(document["html_url"])

    def create_draft_change(
        self,
        *,
        repository_uri: str,
        base_commit: str,
        base_ref: str,
        head_branch: str,
        title: str,
        body: str,
        patch: PatchArtifact,
    ) -> DraftChange:
        if not self._token:
            raise _refused("GitHub draft delivery requires a token", code="delivery.credential_required")
        owner, repository = self._repository(repository_uri)
        with tempfile.TemporaryDirectory(prefix="barista-github-delivery-") as temporary:
            root = Path(temporary) / "repository"
            askpass = Path(temporary) / "askpass.sh"
            askpass.write_text(
                "#!/bin/sh\ncase \"$1\" in *Username*) printf %s x-access-token;; *) printf %s \"$BARISTA_GITHUB_TOKEN\";; esac\n"
            )
            askpass.chmod(0o700)
            env = os.environ.copy()
            env.update(
                {
                    "GIT_ASKPASS": str(askpass),
                    "GIT_TERMINAL_PROMPT": "0",
                    "GIT_CONFIG_NOSYSTEM": "1",
                    "BARISTA_GITHUB_TOKEN": self._token,
                    "GIT_AUTHOR_NAME": "Barista App",
                    "GIT_AUTHOR_EMAIL": "app@barista.invalid",
                    "GIT_COMMITTER_NAME": "Barista App",
                    "GIT_COMMITTER_EMAIL": "app@barista.invalid",
                    "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z",
                    "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z",
                }
            )

            def git(*args: str, input_bytes: bytes | None = None) -> bytes:
                process = subprocess.run(
                    ["git", "-C", str(root), *args],
                    env=env,
                    input=input_bytes,
                    stdin=None if input_bytes is not None else subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=120,
                    check=False,
                )
                if process.returncode != 0:
                    raise _refused(
                        f"GitHub delivery Git operation exited {process.returncode}",
                        code="delivery.git_failed",
                    )
                return process.stdout

            subprocess.run(
                ["git", "init", "--quiet", str(root)],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=120,
                check=True,
            )
            git("remote", "add", "origin", repository_uri)
            git("fetch", "--quiet", "--depth=1", "origin", base_commit)
            git("checkout", "--quiet", "--detach", base_commit)
            git("apply", "--index", "--binary", "-", input_bytes=patch.data)
            git("switch", "--quiet", "-c", head_branch)
            git("commit", "--quiet", "--no-verify", "-m", f"Barista verified change {patch.digest}")
            head_commit = git("rev-parse", "HEAD").decode("ascii").strip()

            existing_ref: str | None = None
            try:
                existing_ref = self.resolve_ref(repository_uri, head_branch)
            except TerminalError as exc:
                if exc.code != "forge.http" or exc.details.get("status") != 404:
                    raise
            if existing_ref is None:
                git("push", "--quiet", "origin", f"HEAD:refs/heads/{head_branch}")
            elif existing_ref != head_commit:
                raise _refused(
                    "GitHub draft branch exists with different content",
                    code="delivery.branch_conflict",
                )

        # Recheck after the authenticated push. The base may have moved during
        # clone/apply/push; never create a PR that silently targets a newer base.
        if self.resolve_ref(repository_uri, base_ref) != base_commit:
            raise _refused(
                "repository base ref moved during delivery",
                code="delivery.moving_base",
                details={"resolved_commit": base_commit},
            )
        existing = self._request(
            "GET",
            f"/repos/{quote(owner)}/{quote(repository)}/pulls?state=open&head={quote(owner + ':' + head_branch)}",
        )
        if isinstance(existing, list) and existing:
            pull = existing[0]
            existing_base = str((pull.get("base") or {}).get("ref", ""))
            if (
                patch.digest not in str(pull.get("body") or "")
                or pull.get("draft") is not True
                or (existing_base and existing_base != base_ref)
            ):
                raise _refused("existing draft does not match delivery", code="delivery.branch_conflict")
        else:
            pull = self._request(
                "POST",
                f"/repos/{quote(owner)}/{quote(repository)}/pulls",
                document={
                    "title": title,
                    "body": body,
                    "head": head_branch,
                    "base": base_ref,
                    "draft": True,
                },
                expected=(201,),
            )
        if not isinstance(pull, dict):
            raise _refused("GitHub pull response is invalid", code="forge.response")
        actual_head = str((pull.get("head") or {}).get("sha") or head_commit)
        if actual_head != head_commit:
            raise _refused("GitHub pull head does not match verified head", code="delivery.head_mismatch")
        return DraftChange(
            url=str(pull.get("html_url", "")),
            repository_uri=repository_uri,
            number=int(pull["number"]),
            base_commit=base_commit,
            base_ref=base_ref,
            head_branch=head_branch,
            head_commit=head_commit,
            title=title,
            body=body,
            patch_digest=patch.digest,
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
        base_ref: str,
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
        head_commit = hashlib.sha1(
            (base_commit + "\0" + patch.digest).encode("ascii")
        ).hexdigest()
        change = DraftChange(
            url=f"fake://pull/{number}",
            repository_uri=repository_uri,
            number=number,
            base_commit=base_commit,
            base_ref=base_ref,
            head_branch=head_branch,
            head_commit=head_commit,
            title=title,
            body=body,
            patch_digest=patch.digest,
        )
        self.changes.append(change)
        return change
