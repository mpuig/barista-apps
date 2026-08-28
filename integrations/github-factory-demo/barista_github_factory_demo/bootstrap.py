"""Explicit GitHub repository/webhook and provider app bootstrap."""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx
from barista_app_sdk import BaristaClient, Config

from .executor import ACCEPTANCE_SCRIPT

SEED_README = """# Barista GitHub Factory demo

Open an issue. The signed webhook launches an ephemeral Factory run and creates
a draft pull request containing `issues/issue-N.md` after independent checks.
"""
_GITHUB_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")

SEED_FILES = {
    "README.md": SEED_README,
    ".barista/accept_issue.py": ACCEPTANCE_SCRIPT,
    "issues/.gitkeep": "",
}


class GitHubAdmin:
    def __init__(self, token: str, *, api_url: str = "https://api.github.com"):
        if not token:
            raise ValueError("GitHub token is required")
        self._token = token
        self._api = api_url.rstrip("/")

    def request(
        self,
        method: str,
        path: str,
        *,
        document: dict | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> dict | list | None:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "barista-github-factory-demo",
        }
        try:
            response = httpx.request(
                method,
                self._api + path,
                headers=headers,
                json=document,
                timeout=30,
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            raise RuntimeError("GitHub bootstrap request failed") from exc
        if response.status_code not in expected:
            raise RuntimeError(
                f"GitHub bootstrap {method} {path} returned HTTP {response.status_code}"
            )
        if response.status_code == 204:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise RuntimeError("GitHub bootstrap returned invalid JSON") from exc

    def ensure_repository(self, owner: str, name: str, *, reuse: bool) -> dict:
        existing = httpx.request(
            "GET",
            f"{self._api}/repos/{quote(owner)}/{quote(name)}",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "barista-github-factory-demo",
            },
            timeout=30,
        )
        if existing.status_code == 200:
            if not reuse:
                raise RuntimeError(
                    "GitHub repository already exists; pass --reuse explicitly"
                )
            document = existing.json()
            if document.get("private"):
                raise RuntimeError(
                    "demo repository must be public for token-free worker acquisition"
                )
            return document
        if existing.status_code != 404:
            raise RuntimeError(
                f"GitHub repository lookup returned HTTP {existing.status_code}"
            )
        user = self.request("GET", "/user")
        if not isinstance(user, dict):
            raise TypeError("GitHub user response is invalid")
        path = (
            "/user/repos"
            if user.get("login", "").lower() == owner.lower()
            else f"/orgs/{quote(owner)}/repos"
        )
        created = self.request(
            "POST",
            path,
            document={
                "name": name,
                "description": "Disposable Barista GitHub Factory webhook demo",
                "private": False,
                "auto_init": False,
                "has_issues": True,
            },
            expected=(201,),
        )
        if not isinstance(created, dict):
            raise TypeError("GitHub repository creation response is invalid")
        return created

    def ensure_seed(self, owner: str, repository: str, *, branch: str = "main") -> None:
        ref_response = httpx.request(
            "GET",
            f"{self._api}/repos/{quote(owner)}/{quote(repository)}/git/ref/heads/{quote(branch, safe='')}",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "barista-github-factory-demo",
            },
            timeout=30,
        )
        if ref_response.status_code not in {200, 404, 409}:
            raise RuntimeError(
                f"GitHub seed branch lookup returned HTTP {ref_response.status_code}"
            )
        branch_exists = ref_response.status_code == 200
        if not branch_exists:
            # GitHub refuses every Git Database endpoint with 409 while a
            # repository is empty. Bootstrap its first commit through Contents,
            # then normalize an account-specific default branch to `main`.
            repository_document = self.request(
                "GET", f"/repos/{quote(owner)}/{quote(repository)}"
            )
            if not isinstance(repository_document, dict):
                raise TypeError("GitHub repository response is invalid")
            default_branch = str(repository_document.get("default_branch") or "main")
            readme_endpoint = (
                f"/repos/{quote(owner)}/{quote(repository)}/contents/README.md"
            )
            readme_response = httpx.request(
                "GET",
                self._api + readme_endpoint,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "barista-github-factory-demo",
                },
                timeout=30,
            )
            if readme_response.status_code == 404:
                self.request(
                    "PUT",
                    readme_endpoint,
                    document={
                        "message": "Seed Barista GitHub Factory demo",
                        "content": base64.b64encode(SEED_README.encode()).decode(),
                    },
                    expected=(201,),
                )
            elif readme_response.status_code == 200:
                try:
                    existing_readme = base64.b64decode(
                        readme_response.json()["content"]
                    ).decode("utf-8")
                except (KeyError, ValueError, UnicodeDecodeError) as exc:
                    raise RuntimeError("existing seed README is unreadable") from exc
                if existing_readme != SEED_README:
                    raise RuntimeError(
                        "existing seed file differs; refusing to overwrite: README.md"
                    )
            else:
                raise RuntimeError(
                    "GitHub seed README lookup returned HTTP "
                    f"{readme_response.status_code}"
                )
            if default_branch != branch:
                self.request(
                    "POST",
                    f"/repos/{quote(owner)}/{quote(repository)}/branches/"
                    f"{quote(default_branch, safe='')}/rename",
                    document={"new_name": branch},
                    expected=(201,),
                )
        for path, content in SEED_FILES.items():
            endpoint = f"/repos/{quote(owner)}/{quote(repository)}/contents/{quote(path, safe='/')}"
            response = httpx.request(
                "GET",
                self._api + endpoint,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "barista-github-factory-demo",
                },
                params={"ref": branch},
                timeout=30,
            )
            if response.status_code == 200:
                current = response.json()
                try:
                    existing = base64.b64decode(current["content"]).decode("utf-8")
                except (KeyError, ValueError, UnicodeDecodeError) as exc:
                    raise RuntimeError(
                        f"existing seed file is unreadable: {path}"
                    ) from exc
                if existing != content:
                    raise RuntimeError(
                        f"existing seed file differs; refusing to overwrite: {path}"
                    )
                continue
            if response.status_code != 404:
                raise RuntimeError(
                    f"GitHub seed lookup for {path} returned HTTP {response.status_code}"
                )
            create = {
                "message": f"Seed Barista demo: {path}",
                "content": base64.b64encode(content.encode()).decode(),
                "branch": branch,
            }
            self.request(
                "PUT",
                endpoint,
                document=create,
                expected=(201,),
            )

    def ensure_webhook(
        self,
        owner: str,
        repository: str,
        *,
        url: str,
        secret: str,
    ) -> int:
        hooks = self.request("GET", f"/repos/{quote(owner)}/{quote(repository)}/hooks")
        if not isinstance(hooks, list):
            raise TypeError("GitHub webhook list response is invalid")
        matches = [
            hook for hook in hooks if (hook.get("config") or {}).get("url") == url
        ]
        document = {
            "name": "web",
            "active": True,
            "events": ["issues", "issue_comment"],
            "config": {
                "url": url,
                "content_type": "json",
                "secret": secret,
                "insecure_ssl": "0",
            },
        }
        if matches:
            hook_id = int(matches[0]["id"])
            self.request(
                "PATCH",
                f"/repos/{quote(owner)}/{quote(repository)}/hooks/{hook_id}",
                document=document,
            )
            return hook_id
        created = self.request(
            "POST",
            f"/repos/{quote(owner)}/{quote(repository)}/hooks",
            document=document,
            expected=(201,),
        )
        if not isinstance(created, dict):
            raise TypeError("GitHub webhook creation response is invalid")
        return int(created["id"])

    def delete_webhook(self, owner: str, repository: str, hook_id: int) -> None:
        self.request(
            "DELETE",
            f"/repos/{quote(owner)}/{quote(repository)}/hooks/{hook_id}",
            expected=(204,),
        )

    def delete_repository(self, owner: str, repository: str) -> None:
        self.request(
            "DELETE",
            f"/repos/{quote(owner)}/{quote(repository)}",
            expected=(204,),
        )


def _load_manifest(path: Path, *, name: str, image: str, digest: str) -> dict:
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("workload digest must be a lowercase full sha256 digest")
    document = json.loads(path.read_text())
    document["name"] = name
    document["workload"]["image"] = image
    document["workload"]["digest"] = digest
    return document


def _write_state(path: Path, state: dict) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(state, sort_keys=True, indent=2) + "\n")
    temporary.chmod(0o600)
    os.replace(temporary, path)


def setup_demo(
    *,
    token: str,
    owner: str,
    repository: str,
    webhook_url: str,
    webhook_secret: str,
    factory_manifest: Path,
    factory_name: str,
    factory_image: str,
    factory_digest: str,
    triage_manifest: Path,
    triage_name: str,
    triage_image: str,
    triage_digest: str,
    worker_manifest: Path,
    worker_name: str,
    worker_image: str,
    worker_digest: str,
    state_path: Path,
    reuse: bool,
    github: GitHubAdmin | None = None,
    client: BaristaClient | None = None,
) -> dict:
    parsed = urlparse(webhook_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != "/webhooks/github"
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "webhook URL must be public HTTPS and end exactly in /webhooks/github"
        )
    if not _GITHUB_SLUG.fullmatch(owner) or not _GITHUB_SLUG.fullmatch(repository):
        raise ValueError("GitHub owner and repository must be simple canonical names")
    if repository.endswith(".git"):
        raise ValueError("GitHub repository name must omit .git")
    if not webhook_secret:
        raise ValueError("webhook secret is required")
    factory = _load_manifest(
        factory_manifest,
        name=factory_name,
        image=factory_image,
        digest=factory_digest,
    )
    # The demo resolves a public issue and runner-owned delivery keeps the
    # GitHub credential in this controller. Install a least-authority Factory
    # variant with only its provider-owned coordinator grant.
    factory_permissions = factory.setdefault("permissions", {})
    factory_permissions["secrets"] = [
        declaration
        for declaration in factory_permissions.get("secrets", [])
        if declaration.get("name") == "BARISTA_HOST_API_TOKEN"
    ]
    triage = _load_manifest(
        triage_manifest,
        name=triage_name,
        image=triage_image,
        digest=triage_digest,
    )
    worker = _load_manifest(
        worker_manifest,
        name=worker_name,
        image=worker_image,
        digest=worker_digest,
    )
    admin = github or GitHubAdmin(token)
    repo = admin.ensure_repository(owner, repository, reuse=reuse)
    if repo.get("full_name") != f"{owner}/{repository}":
        raise RuntimeError("GitHub returned a different repository identity")
    admin.ensure_seed(owner, repository)
    hook_id = admin.ensure_webhook(
        owner,
        repository,
        url=webhook_url,
        secret=webhook_secret,
    )
    state = {
        "schema_version": "v1alpha1",
        "status": "bootstrapping",
        "repository": f"https://github.com/{owner}/{repository}",
        "full_name": f"{owner}/{repository}",
        "hook_id": hook_id,
        "webhook_url": webhook_url,
        "factory_app": f"{factory_name}@{factory['version']}",
        "factory_workload_digest": factory_digest,
        "triage_app": triage_name,
        "triage_workload_digest": triage_digest,
        "worker_app": worker_name,
        "worker_workload_digest": worker_digest,
    }
    # Record external resources before Host API mutation. A failed app install
    # therefore still leaves enough non-secret identity for explicit teardown.
    _write_state(state_path, state)
    own_client = client is None
    host = client or BaristaClient(Config.from_env())
    try:
        host.install_app(factory, idempotency_key=f"github-demo-install-{factory_name}")
        host.install_app(triage, idempotency_key=f"github-demo-install-{triage_name}")
        host.install_app(worker, idempotency_key=f"github-demo-install-{worker_name}")
    finally:
        if own_client:
            host.close()
    state["status"] = "ready"
    _write_state(state_path, state)
    return state


def teardown_demo(
    *,
    token: str,
    state_path: Path,
    delete_repository: bool,
    confirmed: bool,
    github: GitHubAdmin | None = None,
) -> dict:
    state_path = state_path.expanduser().resolve()
    state = json.loads(state_path.read_text())
    owner, repository = state["full_name"].split("/", 1)
    if delete_repository and not confirmed:
        raise ValueError("repository deletion requires --yes-really-delete")
    admin = github or GitHubAdmin(token)
    admin.delete_webhook(owner, repository, int(state["hook_id"]))
    deleted_repository = False
    if delete_repository:
        admin.delete_repository(owner, repository)
        deleted_repository = True
    state_path.unlink()
    return {"webhook_deleted": True, "repository_deleted": deleted_repository}
