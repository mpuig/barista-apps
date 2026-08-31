from __future__ import annotations

from pathlib import Path
import re

from barista_github_factory_demo import (
    ControllerConfig,
    DeliveryStore,
    DemoController,
    create_app,
)
from fastapi.testclient import TestClient
import pytest


class InertExecutor:
    def execute(self, claim):  # pragma: no cover - no background launch in these tests
        raise AssertionError(claim)


class InertProgramExecutor:
    pass


class PresenterForge:
    def __init__(self):
        self.created = []
        self.closed = []

    def latest_demo_issue(self):
        return None

    def ensure_demo_issue(self, key):
        self.created.append(key)
        return {
            "number": 50,
            "html_url": "https://github.com/acme/demo/issues/50",
            "body": (
                "<!-- barista-demo-scenario:v1 "
                f"scenario=deployment-status key={key} -->"
            ),
        }

    def close_demo_issue(self, number):
        self.closed.append(number)

    def close(self):
        pass


TOKEN = "presenter-authority-is-distinct-and-at-least-32-bytes"


def _service(tmp_path: Path):
    config = ControllerConfig(
        repository="https://github.com/acme/demo",
        webhook_secret="webhook-secret",
        github_token="forge-token",
        database=tmp_path / "presenter.sqlite3",
        result_directory=tmp_path / "results",
        presenter_token=TOKEN,
        presenter_public_url="https://factory.example",
    )
    store = DeliveryStore(config.database)
    forge = PresenterForge()
    controller = DemoController(
        config,
        store=store,
        executor=InertExecutor(),
        program_executor=InertProgramExecutor(),
        program_forge=forge,
    )
    return config, store, forge, controller


def _headers():
    return {"Authorization": f"Bearer {TOKEN}"}


def test_presenter_configuration_requires_distinct_bounded_authority(
    tmp_path: Path,
) -> None:
    base = {
        "repository": "https://github.com/acme/demo",
        "webhook_secret": "webhook-secret",
        "github_token": "forge-token",
        "database": tmp_path / "config.sqlite3",
        "result_directory": tmp_path / "results",
    }
    with pytest.raises(ValueError, match="at least 32 bytes"):
        ControllerConfig(**base, presenter_token="short")
    with pytest.raises(ValueError, match="separate credential"):
        ControllerConfig(
            **{
                **base,
                "presenter_token": "forge-token" * 4,
                "github_token": "forge-token" * 4,
            }
        )
    with pytest.raises(ValueError, match="canonical"):
        ControllerConfig(
            **base, presenter_public_url="https://factory.example/presenter"
        )


def test_presenter_is_public_secret_free_and_strictly_protected(tmp_path: Path) -> None:
    config, _store, _forge, controller = _service(tmp_path)
    with TestClient(create_app(config, controller=controller)) as client:
        page = client.get("/presenter")
        assert page.status_code == 200
        assert "Factory presenter" in page.text
        assert TOKEN not in page.text
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
        nonce = re.search(r'<style nonce="([^"]+)">', page.text).group(1)
        assert f"script-src 'nonce-{nonce}'; connect-src 'self'" in page.text
        assert "unsafe-inline" not in page.text
        assert "if(typeof value!=='string'||!value)return null" in page.text
        assert "return state.current_program||null" in page.text
        assert "badge.dataset.state=f.status;li.append(badge)" in page.text
        assert ")).dataset.state" not in page.text
        assert page.headers["cache-control"] == "no-store"

        state = client.get("/presenter/api/state")
        assert state.status_code == 200
        assert state.json()["presenter_controls"] is True
        assert TOKEN not in state.text
        assert state.headers["cache-control"] == "no-store"

        refused = client.post(
            "/presenter/api/scenarios/deployment-status/launch",
            json={"idempotency_key": "demo-12345678"},
        )
        assert refused.status_code == 401
        assert "forge-token" not in refused.text


def test_launch_converges_replays_and_blocks_a_second_current_scenario(
    tmp_path: Path,
) -> None:
    config, _store, forge, controller = _service(tmp_path)
    with TestClient(create_app(config, controller=controller)) as client:
        first = client.post(
            "/presenter/api/scenarios/deployment-status/launch",
            headers=_headers(),
            json={"idempotency_key": "demo-12345678"},
        )
        replay = client.post(
            "/presenter/api/scenarios/deployment-status/launch",
            headers=_headers(),
            json={"idempotency_key": "demo-12345678"},
        )
        competing = client.post(
            "/presenter/api/scenarios/deployment-status/launch",
            headers=_headers(),
            json={"idempotency_key": "demo-87654321"},
        )

        assert first.status_code == 201
        assert replay.status_code == 200
        assert competing.status_code == 200
        assert first.json()["scenario"] == replay.json()["scenario"]
        assert competing.json()["scenario"] == first.json()["scenario"]
        assert competing.json()["reason"] == "current_scenario"
        assert forge.created == ["demo-12345678"]


def test_reset_refuses_active_work_then_settles_terminal_scenario(
    tmp_path: Path,
) -> None:
    config, store, forge, controller = _service(tmp_path)
    with TestClient(create_app(config, controller=controller)) as client:
        launch = client.post(
            "/presenter/api/scenarios/deployment-status/launch",
            headers=_headers(),
            json={"idempotency_key": "demo-12345678"},
        )
        assert launch.status_code == 201
        claim = store.claim(
            delivery_id="delivery-50",
            repository=config.repository,
            issue_number=50,
            issue_uri="https://github.com/acme/demo/issues/50",
            run_name="program-50-brd-attempt-1",
            workflow_kind="program_brd",
            program_id="program-50",
        )
        store.ensure_program("program-50", claim)

        live_state = client.get("/presenter/api/state").json()
        assert live_state["current_program"]["stage"] == "brief"
        assert live_state["current_program"]["next_action"]["owner"] == "Factory"
        assert live_state["current_program"]["attempts"][0]["run_name"] == (
            "program-50-brd-attempt-1"
        )

        active = client.post(
            "/presenter/api/scenarios/reset",
            headers=_headers(),
            json={"idempotency_key": "demo-12345678"},
        )
        assert active.status_code == 409
        assert active.json()["detail"]["code"] == "scenario.active"
        assert forge.closed == []

        store.fail("delivery-50", "retained test failure")
        store.fail_program("program-50", "retained test failure")
        reset = client.post(
            "/presenter/api/scenarios/reset",
            headers=_headers(),
            json={"idempotency_key": "demo-12345678"},
        )
        replay = client.post(
            "/presenter/api/scenarios/reset",
            headers=_headers(),
            json={"idempotency_key": "demo-12345678"},
        )

        assert reset.status_code == 200
        assert replay.status_code == 200
        assert reset.json()["scenario"]["reset_at"] is not None
        assert forge.closed == [50]
        final_state = client.get("/presenter/api/state").json()
        assert final_state["current_scenario"] is None
        assert final_state["programs"][0]["scenario"]["reset_at"] is not None
