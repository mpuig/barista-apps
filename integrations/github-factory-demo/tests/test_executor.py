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
        run_name="github-62924231c5-issue-42-attempt-1",
    )

    run = build_factory_run(config, claim)
    document = run.to_document()

    assert (
        run.name
        == "github-"
        + hashlib.sha256(config.repository.encode()).hexdigest()[:10]
        + "-issue-42-attempt-1"
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
    assert document["operation"] == "issue-sdlc"
    assert document["input"]["value"]["attempt"] == 1
    assert document["input"]["value"]["triage_app"] == "github-issue-triage"
    assert document["input"]["value"]["answers"] == []
    assert document["deliveries"]["question"]["target"] == claim.issue_uri
    assert document["deliveries"]["change"]["options"]["executor"] == "runner"
    assert document["deliveries"]["change"]["target"] == config.repository
    assert document["input"]["value"]["tasks"] == [
        {"id": "issue", "command": ["/usr/local/bin/barista-demo-issue-worker"]}
    ]
    assert "github_token" not in str(document).lower()
    assert "secret" not in str(document).lower()


def test_program_claims_select_fixed_stage_apps_and_branches(tmp_path: Path):
    config = ControllerConfig(
        repository="https://github.com/acme/demo",
        webhook_secret="secret",
        github_token="token",
        database=tmp_path / "db",
        result_directory=tmp_path / "results",
    )
    brd = build_factory_run(
        config,
        Claim(
            "brd",
            config.repository,
            12,
            config.repository + "/issues/12",
            "accepted",
            "github-62924231c5-issue-12-attempt-1",
            workflow_kind="program_brd",
            program_id="program-12",
        ),
    ).to_document()
    feature = build_factory_run(
        config,
        Claim(
            "feature",
            config.repository,
            20,
            config.repository + "/issues/20",
            "accepted",
            "github-62924231c5-issue-20-attempt-1",
            workflow_kind="feature",
            program_id="program-12",
            feature_id="status-api",
        ),
    ).to_document()
    assert brd["operation"] == "product-brief"
    assert brd["input"]["value"]["worker_app"] == "github-brd-author"
    assert brd["input"]["value"]["acceptance"]["command"][-1] == "brd"
    assert (
        brd["deliveries"]["change"]["options"]["head_branch"]
        == "barista/program-12-brd"
    )
    assert feature["operation"] == "issue-sdlc"
    assert feature["input"]["value"]["worker_app"] == "github-feature-worker"
    assert feature["input"]["value"]["acceptance"]["command"][-1] == "status-api"
    assert (
        feature["deliveries"]["change"]["options"]["head_branch"]
        == "barista/program-12-status-api"
    )
    assert "token" not in repr(brd) + repr(feature)


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
        "github-62924231c5-issue-5-attempt-1",
    )
    retry = Claim(
        "delivery-retry",
        config.repository,
        5,
        config.repository + "/issues/5",
        "accepted",
        "github-62924231c5-issue-5-attempt-1",
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
