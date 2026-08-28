from __future__ import annotations

import hashlib
from pathlib import Path

from barista_github_factory_demo import Claim, ControllerConfig, build_factory_run


def test_issue_claim_compiles_to_one_runner_owned_factory_run(tmp_path: Path):
    config = ControllerConfig(
        repository="https://github.com/acme/demo",
        webhook_secret="secret",
        github_token="token",
        factory_app="github-demo-factory@0.5.3",
        worker_app="github-issue-worker",
        database=tmp_path / "db",
        result_directory=tmp_path / "results",
    )
    claim = Claim(
        delivery_id="delivery-1",
        repository=config.repository,
        issue_number=42,
        issue_uri=config.repository + "/issues/42",
        status="accepted",
        run_name="ignored-by-compiler",
    )

    run = build_factory_run(config, claim)
    document = run.to_document()

    assert (
        run.name
        == "github-"
        + hashlib.sha256(config.repository.encode()).hexdigest()[:10]
        + "-issue-42"
    )
    assert document["bindings"] == {
        "objective": {
            "kind": "com.github.issue",
            "uri": config.repository + "/issues/42",
        },
        "workspace": {
            "kind": "sh.barista.git.repository",
            "uri": config.repository,
            "ref": "main",
        },
    }
    assert document["deliveries"]["change"]["options"]["executor"] == "runner"
    assert document["deliveries"]["change"]["target"] == config.repository
    assert document["input"]["value"]["tasks"] == [
        {"id": "issue", "command": ["/usr/local/bin/barista-demo-issue-worker"]}
    ]
    assert "github_token" not in str(document).lower()
    assert "secret" not in str(document).lower()


def test_run_and_branch_identity_are_stable_across_webhook_retries(tmp_path: Path):
    config = ControllerConfig(
        repository="https://github.com/acme/demo",
        webhook_secret="secret",
        github_token="token",
        database=tmp_path / "db",
        result_directory=tmp_path / "results",
    )
    one = Claim(
        "delivery-1",
        config.repository,
        5,
        config.repository + "/issues/5",
        "accepted",
        "unused",
    )
    retry = Claim(
        "delivery-retry",
        config.repository,
        5,
        config.repository + "/issues/5",
        "accepted",
        "unused",
    )

    first = build_factory_run(config, one).to_document()
    second = build_factory_run(config, retry).to_document()

    assert first["name"] == second["name"]
    assert (
        first["deliveries"]["change"]["options"]["head_branch"]
        == second["deliveries"]["change"]["options"]["head_branch"]
    )
    # Delivery IDs remain controller audit data and do not perturb run idempotency.
    assert first == second
