"""CLI: run the conformance suite against a configured Host API endpoint.

    barista-conformance --endpoint http://localhost:8088 --report report.json
    BARISTA_HOST_API_ENDPOINT=... barista-conformance --standalone

Exit code is non-zero when the provider is not conformant, so CI can gate on it.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import CONTRACT_VERSION, SUITE_VERSION
from .config import DelegatedProbe, ProviderConfig
from .report import evaluate_conformance
from .runner import run_conformance


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="barista-conformance")
    parser.add_argument("--endpoint", help="Host API base URL (or BARISTA_HOST_API_ENDPOINT).")
    parser.add_argument("--token", help="Bearer token (prefer --token-env).")
    parser.add_argument("--token-env", help="Env var to read the bearer token from.")
    parser.add_argument("--provider-name", default="unknown")
    parser.add_argument("--provider-version", default="unknown")
    parser.add_argument(
        "--standalone",
        action="store_true",
        help="Enforce the mandatory Cloud-absent profile (block Cloud DNS, refuse proprietary imports).",
    )
    parser.add_argument("--report", help="Write the machine-readable JSON report here.")
    args = parser.parse_args(argv)

    endpoint = args.endpoint
    if not endpoint:
        try:
            config = ProviderConfig.from_env()
        except ValueError as exc:
            parser.error(str(exc))
        config.standalone = config.standalone or args.standalone
    else:
        config = ProviderConfig(
            endpoint=endpoint.rstrip("/"),
            token=args.token,
            token_env=args.token_env,
            provider_name=args.provider_name,
            provider_version=args.provider_version,
            standalone=args.standalone,
            # Delegated credentials always come from the environment — they are
            # credentials, and argv is not a place to put one.
            delegated_probe=DelegatedProbe.from_env(),
        )

    report = run_conformance(
        config, contract_version=CONTRACT_VERSION, suite_version=SUITE_VERSION
    )
    doc = report.to_dict()

    if args.report:
        with open(args.report, "w") as fh:
            json.dump(doc, fh, indent=2, sort_keys=True)

    conformant, violations = evaluate_conformance(report)
    summary = report.summary()
    print(
        f"provider={doc['provider']['name']}@{doc['provider']['version']} "
        f"contract={doc['contract_version']} suite={doc['suite_version']} "
        f"standalone={doc['standalone']}"
    )
    print(
        f"cases: passed={summary['passed']} failed={summary['failed']} "
        f"skipped={summary['skipped']} -> conformant={conformant}"
    )
    for v in violations:
        print(f"  violation: {v}")
    return 0 if conformant else 1


if __name__ == "__main__":
    sys.exit(main())
