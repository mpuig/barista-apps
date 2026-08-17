"""CLI entry point for Lift (thin; see barista_app_lift.Lift for the library)."""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="barista-lift")
    parser.add_argument("--mode", choices=["exact", "semantic", "auto"], default="auto")
    parser.add_argument("--source-endpoint", required=True)
    parser.add_argument("--target-endpoint", required=True)
    parser.parse_args(argv)
    # The library API (barista_app_lift.Lift) is the supported surface; a full
    # CLI that wires concrete capsule/adapter clients lands with the Host API
    # capsule endpoints. Fail honestly rather than pretend.
    print("barista-lift CLI wiring lands with the Host API capsule endpoints; "
          "use the barista_app_lift.Lift library API for now.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
