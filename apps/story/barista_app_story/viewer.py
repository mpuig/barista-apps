"""Static, non-executable Session Story viewer.

Renders a story bundle to a self-contained HTML page with no scripts and every
value HTML-escaped — a story is knowledge to read, never code to run.
"""

from __future__ import annotations

import html
from typing import Any


def render_html(bundle: dict) -> str:
    def esc(v: Any) -> str:
        return html.escape(str(v), quote=True)

    title = esc(bundle.get("title") or f"Session Story {bundle.get('story_id', '')[:19]}")
    rows = []
    for rec in bundle.get("records", []):
        parts = [f"<span class=\"type\">{esc(rec.get('type'))}</span>"]
        if rec.get("time"):
            parts.append(f"<span class=\"time\">{esc(rec['time'])}</span>")
        if rec.get("text"):
            parts.append(f"<pre>{esc(rec['text'])}</pre>")
        if rec.get("artifact"):
            a = rec["artifact"]
            parts.append(f"<code>{esc(a.get('name'))} · {esc(a.get('media_type'))} · {esc(a.get('digest'))}</code>")
        rows.append(f"<li data-seq=\"{esc(rec.get('seq'))}\">{''.join(parts)}</li>")

    removed = bundle.get("removed", [])
    removed_html = "".join(
        f"<li>{esc(r['category'])}: {esc(r['count'])}</li>" for r in removed
    )
    policy = bundle.get("redaction_policy", {})

    # No <script> anywhere; content-security stance is 'static document'.
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>{title}</title>
<style>
 body {{ font: 15px/1.5 system-ui, sans-serif; max-width: 60rem; margin: 2rem auto; padding: 0 1rem; }}
 .meta {{ color: #666; font-size: 13px; }}
 ol {{ list-style: none; padding: 0; }}
 li {{ border-left: 3px solid #ddd; padding: .3rem .8rem; margin: .5rem 0; }}
 .type {{ font-weight: 600; text-transform: uppercase; font-size: 11px; color: #a15; }}
 .time {{ color: #999; font-size: 12px; margin-left: .5rem; }}
 pre {{ white-space: pre-wrap; margin: .3rem 0 0; }}
 code {{ color: #357; }}
</style>
</head>
<body>
<h1>{title}</h1>
<p class="meta">story id {esc(bundle.get('story_id'))} · redaction {esc(policy.get('name'))}/{esc(policy.get('version'))} · created {esc(bundle.get('created_at'))}</p>
<ol>{''.join(rows)}</ol>
<h2>Removed</h2>
<ul>{removed_html or '<li>nothing</li>'}</ul>
</body>
</html>
"""
