from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from barista_app_sdk import RunBinding, errors
from barista_app_sdk.sources import (
    GIT_REPOSITORY_KIND,
    materialize_git_repository,
    resolve_git_commit,
    resolve_local_objective,
)


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "source"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("base\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-qm", "base")
    return repo, _git(repo, "rev-parse", "HEAD")


def _binding(repo: Path, **options) -> RunBinding:
    value = {
        "kind": GIT_REPOSITORY_KIND,
        "uri": repo.as_uri(),
        "ref": "main",
    }
    if options:
        value["options"] = options
    return RunBinding.parse(value)


def test_git_binding_resolves_once_and_materializes_detached_exact_commit(tmp_path):
    repo, commit = _repository(tmp_path)
    destination = tmp_path / "workspace"

    resolved = materialize_git_repository(
        _binding(repo), destination, max_bytes=4 * 1024 * 1024
    )

    assert resolved.commit == commit
    assert _git(destination, "rev-parse", "HEAD") == commit
    detached = subprocess.run(
        ["git", "-C", str(destination), "symbolic-ref", "-q", "HEAD"],
        capture_output=True,
    )
    assert detached.returncode != 0
    assert (destination / "README.md").read_text() == "base\n"
    assert resolved.to_result_binding()["resolved_identity"] == commit


def test_git_size_violation_removes_only_new_destination(tmp_path):
    repo, _ = _repository(tmp_path)
    destination = tmp_path / "workspace"

    with pytest.raises(errors.TerminalError) as caught:
        materialize_git_repository(_binding(repo), destination, max_bytes=1)

    assert caught.value.code == "binding.git_size_limit"
    assert not destination.exists()
    assert repo.exists()


def test_git_submodules_require_explicit_behavior(tmp_path):
    repo, _ = _repository(tmp_path)
    (repo / ".gitmodules").write_text('[submodule "dep"]\n path = dep\n url = ../dep\n')
    _git(repo, "add", ".gitmodules")
    _git(repo, "commit", "-qm", "declare submodule")

    with pytest.raises(errors.TerminalError) as caught:
        materialize_git_repository(_binding(repo), tmp_path / "refused", max_bytes=4 * 1024 * 1024)
    assert caught.value.code == "binding.git_submodules_refused"

    accepted = materialize_git_repository(
        _binding(repo, submodules="ignore"),
        tmp_path / "accepted",
        max_bytes=4 * 1024 * 1024,
    )
    assert accepted.submodules == "ignored"
    assert not (accepted.workspace / "dep").exists()


def test_git_lfs_requires_explicit_behavior(tmp_path):
    repo, _ = _repository(tmp_path)
    (repo / ".gitattributes").write_text("*.bin filter=lfs diff=lfs merge=lfs -text\n")
    (repo / "asset.bin").write_text("pointer contents\n")
    _git(repo, "add", ".gitattributes", "asset.bin")
    _git(repo, "commit", "-qm", "declare lfs")

    with pytest.raises(errors.TerminalError) as caught:
        materialize_git_repository(_binding(repo), tmp_path / "refused-lfs", max_bytes=4 * 1024 * 1024)
    assert caught.value.code == "binding.git_lfs_refused"

    accepted = materialize_git_repository(
        _binding(repo, lfs="ignore"),
        tmp_path / "accepted-lfs",
        max_bytes=4 * 1024 * 1024,
    )
    assert accepted.lfs == "pointer-files"


def test_materialization_refuses_to_follow_a_ref_that_moved(tmp_path, monkeypatch):
    import barista_app_sdk.sources as sources

    repo, _ = _repository(tmp_path)
    destination = tmp_path / "moving"
    monkeypatch.setattr(sources, "resolve_git_commit", lambda *args, **kwargs: "f" * 40)

    with pytest.raises(errors.TerminalError) as caught:
        materialize_git_repository(_binding(repo), destination, max_bytes=4 * 1024 * 1024)

    assert caught.value.code == "binding.git_moving_ref"
    assert not destination.exists()


def test_local_git_binding_refuses_credential_material(tmp_path):
    repo, _ = _repository(tmp_path)
    with pytest.raises(errors.InvalidRequestError) as caught:
        resolve_git_commit(_binding(repo), credential_value="not-on-command-line")
    assert caught.value.code == "binding.git_local_credential"


def test_local_objective_is_bounded_utf8_and_content_addressed(tmp_path):
    objective = tmp_path / "objective.md"
    objective.write_text("Fix the parser.\n")
    binding = RunBinding.parse(
        {
            "kind": "sh.barista.specification",
            "uri": objective.as_uri(),
            "options": {"media_type": "text/markdown"},
        }
    )

    resolved = resolve_local_objective(binding, max_bytes=1024)

    assert resolved.content == b"Fix the parser.\n"
    assert resolved.digest == "sha256:" + hashlib.sha256(resolved.content).hexdigest()
    assert resolved.to_result_binding()["metadata"]["media_type"] == "text/markdown"


def test_local_objective_size_limit_is_checked_before_read(tmp_path):
    objective = tmp_path / "objective.md"
    objective.write_text("too large")
    binding = RunBinding.parse(
        {"kind": "sh.barista.text", "uri": objective.as_uri()}
    )

    with pytest.raises(errors.TerminalError) as caught:
        resolve_local_objective(binding, max_bytes=3)

    assert caught.value.code == "binding.objective_size_limit"
