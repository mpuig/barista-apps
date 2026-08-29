"""CLI for serving, bootstrapping, and tearing down the GitHub Factory demo."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .app import create_app
from .bootstrap import setup_demo, teardown_demo
from .config import ControllerConfig
from .live_acceptance import run_live_acceptance
from .project_setup import setup_project

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_STATE = Path(".barista-github-demo.json")


def _secret(environment_name: str) -> str:
    value = os.environ.get(environment_name, "")
    if not value:
        raise SystemExit(
            f"required secret environment variable is unset: {environment_name}"
        )
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="barista-github-demo")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Serve signed GitHub webhooks.")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8098)

    setup = sub.add_parser(
        "setup", help="Create/reuse the demo repo, seed, webhook, and apps."
    )
    setup.add_argument("--owner", required=True)
    setup.add_argument("--repository", required=True)
    setup.add_argument("--webhook-url", required=True)
    setup.add_argument("--github-token-env", default="GH_TOKEN")
    setup.add_argument("--webhook-secret-env", default="BARISTA_GITHUB_WEBHOOK_SECRET")
    setup.add_argument("--factory-name", default="github-demo-factory")
    setup.add_argument("--factory-image", required=True)
    setup.add_argument("--factory-digest", required=True)
    setup.add_argument(
        "--factory-manifest",
        type=Path,
        default=REPOSITORY_ROOT / "apps/factory/manifest.json",
    )
    setup.add_argument("--triage-name", default="github-issue-triage")
    setup.add_argument("--triage-image", required=True)
    setup.add_argument("--triage-digest", required=True)
    setup.add_argument(
        "--triage-manifest",
        type=Path,
        default=REPOSITORY_ROOT / "apps/github-issue-triage/manifest.json",
    )
    setup.add_argument("--worker-name", default="github-issue-worker")
    setup.add_argument("--worker-image", required=True)
    setup.add_argument("--worker-digest", required=True)
    setup.add_argument(
        "--worker-manifest",
        type=Path,
        default=REPOSITORY_ROOT / "apps/github-issue-worker/manifest.json",
    )
    setup.add_argument("--state", type=Path, default=DEFAULT_STATE)
    setup.add_argument("--reuse", action="store_true")

    teardown = sub.add_parser(
        "teardown", help="Delete resources recorded by setup state."
    )
    teardown.add_argument("--github-token-env", default="GH_TOKEN")
    teardown.add_argument("--state", type=Path, default=DEFAULT_STATE)
    teardown.add_argument("--delete-repository", action="store_true")
    teardown.add_argument("--yes-really-delete", action="store_true")

    status = sub.add_parser("status", help="Print non-secret setup state.")
    status.add_argument("--state", type=Path, default=DEFAULT_STATE)

    accept = sub.add_parser("accept", help="Run opt-in real-GitHub acceptance.")
    accept.add_argument("--controller-url", required=True)
    accept.add_argument(
        "--output", type=Path, default=Path("github-factory-live-evidence.json")
    )
    accept.add_argument("--timeout", type=float, default=1800)
    accept.add_argument(
        "--clarify",
        action="store_true",
        help="Exercise clarification, an authorized answer, and a fresh attempt.",
    )

    project = sub.add_parser(
        "project-setup", help="Create or validate the non-authoritative demo project."
    )
    project.add_argument("--owner", required=True)
    project.add_argument(
        "--owner-kind", choices=("user", "organization"), default="user"
    )
    project.add_argument("--title", default="Barista product program")
    project.add_argument("--project-number", type=int)
    project.add_argument("--github-token-env", default="BARISTA_GITHUB_PROJECT_TOKEN")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "serve":
        import uvicorn

        config = ControllerConfig.from_env()
        uvicorn.run(create_app(config), host=args.host, port=args.port)
        return 0
    if args.command == "setup":
        state = setup_demo(
            token=_secret(args.github_token_env),
            owner=args.owner,
            repository=args.repository,
            webhook_url=args.webhook_url,
            webhook_secret=_secret(args.webhook_secret_env),
            factory_manifest=args.factory_manifest,
            factory_name=args.factory_name,
            factory_image=args.factory_image,
            factory_digest=args.factory_digest,
            triage_manifest=args.triage_manifest,
            triage_name=args.triage_name,
            triage_image=args.triage_image,
            triage_digest=args.triage_digest,
            worker_manifest=args.worker_manifest,
            worker_name=args.worker_name,
            worker_image=args.worker_image,
            worker_digest=args.worker_digest,
            state_path=args.state,
            reuse=args.reuse,
        )
        print(json.dumps(state, indent=2, sort_keys=True))
        print("\nController environment:")
        print(f"export BARISTA_GITHUB_REPOSITORY={state['repository']!r}")
        print(f"export BARISTA_FACTORY_APP={state['factory_app']!r}")
        print(f"export BARISTA_FACTORY_TRIAGE_APP={state['triage_app']!r}")
        print(f"export BARISTA_FACTORY_WORKER_APP={state['worker_app']!r}")
        print(
            "# Keep GH_TOKEN and BARISTA_GITHUB_WEBHOOK_SECRET set; their values are never printed."
        )
        return 0
    if args.command == "teardown":
        result = teardown_demo(
            token=_secret(args.github_token_env),
            state_path=args.state,
            delete_repository=args.delete_repository,
            confirmed=args.yes_really_delete,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "status":
        print(args.state.expanduser().read_text(), end="")
        return 0
    if args.command == "accept":
        evidence = run_live_acceptance(
            ControllerConfig.from_env(),
            controller_url=args.controller_url,
            output=args.output,
            timeout=args.timeout,
            clarify=args.clarify,
        )
        print(json.dumps(evidence, indent=2, sort_keys=True))
        return 0
    if args.command == "project-setup":
        project = setup_project(
            token=_secret(args.github_token_env),
            owner=args.owner,
            owner_kind=args.owner_kind,
            title=args.title,
            project_number=args.project_number,
        )
        print(json.dumps(project, indent=2, sort_keys=True))
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
