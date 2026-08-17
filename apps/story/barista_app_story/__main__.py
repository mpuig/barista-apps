"""CLI: build a Session Story from a JSON record file and emit bundle + HTML.

    barista-story build records.json --created-at 2026-08-17T00:00:00Z \
        --title "My session" --out story.json --html story.html
"""

from __future__ import annotations

import argparse
import json
import sys

from .story import Source, StoryBuilder
from .viewer import render_html


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="barista-story")
    sub = parser.add_subparsers(dest="cmd", required=True)
    build = sub.add_parser("build", help="Build a story bundle from selected records.")
    build.add_argument("records", help="JSON file: a list of knowledge records.")
    build.add_argument("--created-at", required=True, help="Fixed timestamp (keeps the story id deterministic).")
    build.add_argument("--title")
    build.add_argument("--app")
    build.add_argument("--out", help="Write the story JSON here (default stdout).")
    build.add_argument("--html", help="Also write a static HTML view here.")
    args = parser.parse_args(argv)

    records = json.loads(open(args.records).read())
    bundle = StoryBuilder().build(
        records, created_at=args.created_at, title=args.title,
        source=Source(app=args.app) if args.app else None,
    )
    out = json.dumps(bundle, indent=1)
    if args.out:
        open(args.out, "w").write(out)
    else:
        print(out)
    if args.html:
        open(args.html, "w").write(render_html(bundle))
    print(f"story_id={bundle['story_id']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
