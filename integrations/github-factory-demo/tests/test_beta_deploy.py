from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[1] / "deploy-beta.sh"
PROVISION = Path(__file__).resolve().parents[1] / "provision-beta.py"
BOOTSTRAP = Path(__file__).resolve().parents[1] / "bootstrap-beta.py"


def test_beta_deploy_parses_and_refuses_mutation_before_remote_access():
    bash = shutil.which("bash")
    assert bash
    parsed = subprocess.run(
        [bash, "-n", str(DEPLOY)], capture_output=True, text=True, check=False
    )
    assert parsed.returncode == 0, parsed.stderr
    text = DEPLOY.read_text()
    preflight = text.index("status --porcelain --untracked-files=normal")
    remote_main = text.index("git ls-remote --exit-code")
    first_remote_mutation = text.index('copy_source "$CP_HOST"')
    assert preflight < first_remote_mutation
    assert remote_main < first_remote_mutation
    assert 'DEPLOY_REV" != "$REMOTE_MAIN' in text


def test_beta_deploy_is_additive_and_excludes_environment_and_state():
    text = DEPLOY.read_text()
    assert "--delete" not in text
    assert "--exclude '.env'" in text
    assert "--exclude '**/.env'" in text
    assert "--exclude '*.sqlite3'" in text
    assert "--exclude 'github-factory-results'" in text
    assert "/etc/barista/github-factory-demo.env.tmp" not in text


def test_controller_preserves_shared_cloud_mtls_directory_traversal():
    deploy = DEPLOY.read_text()
    provision = PROVISION.read_text()
    assert "install -d -o root -g root -m 0711 /etc/barista" in deploy
    assert "install -d -o root -g root -m 0711 /etc/barista" in provision
    assert "-m 0700 /etc/barista" not in deploy
    assert "-m 0700 /etc/barista" not in provision


def test_beta_images_use_node_registry_response_digests():
    text = DEPLOY.read_text()
    assert "127.0.0.1:5000/barista-factory" in text
    assert "127.0.0.1:5000/barista-github-issue-worker" in text
    assert "127.0.0.1:5000/barista-github-product-worker" in text
    assert "docker-content-digest:" in text.lower()
    assert "^sha256:[0-9a-f]{64}$" in text
    assert "docker inspect" not in text
    assert ".github-factory-images.json" in text


def test_controller_unit_is_unprivileged_hardened_and_secret_separated():
    text = DEPLOY.read_text()
    for expected in (
        "User=barista",
        "Group=barista",
        "NoNewPrivileges=yes",
        "PrivateTmp=yes",
        "PrivateDevices=yes",
        "ProtectSystem=strict",
        "ProtectHome=yes",
        "ReadWritePaths=/var/lib/barista-github-factory-demo",
        "ConditionPathExists=/etc/barista/github-factory-demo.env",
        "EnvironmentFile=/etc/barista/github-factory-demo.env",
        "--host 127.0.0.1 --port 8098",
    ):
        assert expected in text
    assert (
        "controller environment not provisioned; unit installed but not started" in text
    )


def test_provisioning_sends_secret_environment_on_stdin_not_argv():
    text = PROVISION.read_text()
    assert "input=environment" in text
    assert "github_token_file" in text
    assert "host_token_file" in text
    assert "webhook_secret_file" in text
    assert "project_token_file" in text
    assert (
        "github_token"
        not in text[text.index("remote = (") : text.index("subprocess.run([*ssh")]
    )
    assert (
        "project_token"
        not in text[text.index("remote = (") : text.index("subprocess.run([*ssh")]
    )
    assert "BARISTA_GITHUB_TOKEN=" not in text
    assert "BARISTA_GITHUB_PROJECT_TOKEN=" not in text
    assert "BARISTA_HOST_API_TOKEN=" not in text
    assert "chmod 600 /etc/barista/github-factory-demo.env.tmp" in text


def test_bootstrap_uses_separate_github_cli_authority_and_exact_image_state():
    text = BOOTSTRAP.read_text()
    assert '["gh", "auth", "token"]' in text
    assert "github-factory-images.json" in text
    assert 'factory_name="github-demo-factory"' in text
    assert 'worker_name="github-issue-worker"' in text
    assert '"github-brd-author"' in text
    assert '"github-feature-planner"' in text
    assert '"github-feature-worker"' in text
    assert (
        'webhook_url="https://github-factory.beta.barista.sh/webhooks/github"' in text
    )
    assert 'triage_name="github-issue-triage"' in text
    assert "bootstrap_token" not in text[text.index("print(json.dumps") :]
