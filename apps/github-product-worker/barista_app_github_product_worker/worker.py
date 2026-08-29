"""Deterministic, closed product-program reference worker commands."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

MAX_OBJECTIVE_BYTES = 64 * 1024
_FEATURE_MARKER = re.compile(
    r"<!-- barista-program-feature:v1 program=([a-z0-9-]{1,160}) "
    r"feature=([a-z0-9-]{1,40}) plan=(sha256:[0-9a-f]{64}) -->"
)


class ObjectiveError(ValueError):
    """A coordinator-owned objective envelope is malformed."""


def _canonical(document: dict) -> bytes:
    return (
        json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode()


def _read(path_name: str) -> dict:
    path = os.environ.get(path_name)
    if not path:
        raise ObjectiveError(f"{path_name} is required")
    raw = Path(path).read_bytes()
    if not raw or len(raw) > MAX_OBJECTIVE_BYTES:
        raise ObjectiveError("objective size is invalid")
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ObjectiveError("objective must be UTF-8 JSON") from exc
    if not isinstance(document, dict):
        raise ObjectiveError("objective must be an object")
    return document


def _issue(document: Mapping[str, Any]) -> tuple[int, str, str, list[dict]]:
    allowed = {"number", "title", "body", "state", "factory_context"}
    if set(document) - allowed:
        raise ObjectiveError("issue objective contains unsupported fields")
    number = document.get("number")
    title = document.get("title")
    body = document.get("body")
    if (
        not isinstance(number, int)
        or isinstance(number, bool)
        or number <= 0
        or not isinstance(title, str)
        or not title.strip()
        or len(title) > 500
        or not isinstance(body, str)
        or len(body) > 32 * 1024
    ):
        raise ObjectiveError("issue objective is invalid")
    context = document.get("factory_context") or {}
    answers = context.get("answers", []) if isinstance(context, Mapping) else []
    if not isinstance(answers, list) or len(answers) > 20:
        raise ObjectiveError("issue answer context is invalid")
    return number, title, body, answers


def _issue_uri(number: int) -> str:
    value = os.environ.get("BARISTA_OBJECTIVE_URI", "")
    parsed = urlparse(value)
    parts = [part for part in parsed.path.split("/") if part]
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or len(parts) != 4
        or parts[2] != "issues"
        or parts[3] != str(number)
        or value != f"https://github.com/{'/'.join(parts)}"
    ):
        raise ObjectiveError("objective URI is not the expected GitHub issue")
    return value


def _workspace() -> Path:
    root = Path.cwd().resolve()
    if not (root / ".git").exists():
        raise ObjectiveError("workspace is not a Git checkout")
    return root


def _write_issue_record(
    root: Path, number: int, title: str, body: str, uri: str
) -> None:
    target = root / "issues" / f"issue-{number}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        f"# Issue {number}: {title}\n\n"
        f"Source: {uri}\n\n"
        "State: open\n\n"
        "## Objective\n\n"
        f"{body.rstrip()}\n",
        encoding="utf-8",
    )


def author_brd(document: Mapping[str, Any], root: Path) -> Path:
    number, title, body, answers = _issue(document)
    uri = _issue_uri(number)
    if "[barista:product-program]" not in body.casefold():
        raise ObjectiveError("product-program marker is absent")
    decisions = [
        str(answer.get("body", "")).strip()
        for answer in answers
        if isinstance(answer, Mapping) and str(answer.get("body", "")).strip()
    ]
    if not decisions:
        decisions = ["Use the smallest deterministic one-container reference product."]
    _write_issue_record(root, number, title, body, uri)
    target = root / "docs" / "brd" / f"program-{number}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        f"# BRD: {title}\n\n"
        f"Program issue: {uri}\n\n"
        "## Problem\n\n"
        "Operators need a compact deployment status board that demonstrates a complete, human-approved product workflow.\n\n"
        "## Product contract\n\n"
        "A single OCI image runs one container. Its Python backend serves the compiled frontend and JSON API. SQLite state lives at the declared `/data` writable binding.\n\n"
        "## Human decisions\n\n"
        + "".join(f"- {decision}\n" for decision in decisions)
        + "\n## Scope\n\n"
        "- Health and revision API.\n"
        "- SQLite-backed deployment-event API.\n"
        "- Responsive browser dashboard served by the backend.\n\n"
        "## Acceptance\n\n"
        "- The repository contains one multi-stage Dockerfile producing one runtime container.\n"
        "- The backend serves `/api/health`, `/api/events`, and compiled frontend assets.\n"
        "- SQLite uses `BARISTA_DEMO_DB` under `/data` by default.\n"
        "- Deterministic repository tests pass without forge, model, or Host API authority.\n",
        encoding="utf-8",
    )
    return target


_PLAN = (
    {
        "id": "status-api",
        "title": "Add the status API and container skeleton",
        "summary": "Create the one-container Python service with a revision-aware health endpoint.",
        "acceptance_criteria": [
            "GET /api/health returns status, revision, and service identity.",
            "The runtime is represented by one Dockerfile and one container command.",
        ],
        "dependencies": [],
    },
    {
        "id": "event-store",
        "title": "Add SQLite deployment-event storage",
        "summary": "Persist bounded deployment events through the backend JSON API.",
        "acceptance_criteria": [
            "POST and GET /api/events use SQLite persistence.",
            "The database defaults to the declared /data writable binding.",
        ],
        "dependencies": ["status-api"],
    },
    {
        "id": "dashboard",
        "title": "Add the compiled deployment dashboard",
        "summary": "Build and serve a responsive frontend from the same backend container.",
        "acceptance_criteria": [
            "The Docker build compiles frontend assets in a build stage.",
            "The backend serves the dashboard and its assets from the runtime image.",
        ],
        "dependencies": ["event-store"],
    },
)


def plan_features(document: Mapping[str, Any], root: Path) -> dict:
    if (
        set(document)
        != {
            "schema_version",
            "program",
            "approved_commit",
            "brd_path",
            "brd_digest",
        }
        or document.get("schema_version") != "v1alpha1"
    ):
        raise ObjectiveError("planning objective has invalid fields")
    program = document.get("program")
    commit = document.get("approved_commit")
    relative = document.get("brd_path")
    expected = document.get("brd_digest")
    if (
        not isinstance(program, str)
        or not program
        or len(program) > 160
        or not isinstance(commit, str)
        or re.fullmatch(r"[0-9a-f]{40}", commit) is None
        or not isinstance(relative, str)
        or relative.startswith("/")
        or ".." in Path(relative).parts
        or not isinstance(expected, str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", expected) is None
    ):
        raise ObjectiveError("planning objective identity is invalid")
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ObjectiveError("BRD path escapes workspace") from exc
    raw = path.read_bytes()
    actual = "sha256:" + hashlib.sha256(raw).hexdigest()
    if actual != expected or not raw.startswith(b"# BRD:"):
        raise ObjectiveError("approved BRD bytes do not match")
    return {
        "schema_version": "v1alpha1",
        "program": program,
        "approved_commit": commit,
        "features": list(_PLAN),
    }


_STATUS_SERVER = """from __future__ import annotations

import json
import os
from pathlib import Path
from wsgiref.simple_server import make_server

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "web" / "dist"


def _json(start_response, status: str, document: dict | list):
    raw = json.dumps(document, sort_keys=True).encode()
    start_response(status, [("Content-Type", "application/json"), ("Content-Length", str(len(raw)))])
    return [raw]


def application(environ, start_response):
    path = environ.get("PATH_INFO", "/")
    if path == "/api/health":
        return _json(start_response, "200 OK", {"service": "barista-deployment-board", "status": "ok", "revision": os.getenv("BARISTA_DEMO_REVISION", "dev")})
    target = STATIC / ("index.html" if path == "/" else path.lstrip("/"))
    if target.is_file() and STATIC in target.resolve().parents:
        raw = target.read_bytes()
        start_response("200 OK", [("Content-Type", "text/html" if target.suffix == ".html" else "application/octet-stream"), ("Content-Length", str(len(raw)))])
        return [raw]
    return _json(start_response, "404 Not Found", {"error": "not_found"})


if __name__ == "__main__":
    with make_server("0.0.0.0", int(os.getenv("PORT", "8080")), application) as server:
        server.serve_forever()
"""

_EVENT_SERVER = """from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from wsgiref.simple_server import make_server

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "web" / "dist"
DB = Path(os.getenv("BARISTA_DEMO_DB", "/data/demo.sqlite3"))


def _connection():
    DB.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB)
    connection.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, revision TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
    return connection


def _json(start_response, status: str, document: dict | list):
    raw = json.dumps(document, sort_keys=True).encode()
    start_response(status, [("Content-Type", "application/json"), ("Content-Length", str(len(raw)))])
    return [raw]


def application(environ, start_response):
    path = environ.get("PATH_INFO", "/")
    method = environ.get("REQUEST_METHOD", "GET")
    if path == "/api/health" and method == "GET":
        return _json(start_response, "200 OK", {"service": "barista-deployment-board", "status": "ok", "revision": os.getenv("BARISTA_DEMO_REVISION", "dev")})
    if path == "/api/events" and method == "GET":
        with _connection() as connection:
            rows = connection.execute("SELECT id, revision, status, created_at FROM events ORDER BY id DESC LIMIT 100").fetchall()
        return _json(start_response, "200 OK", [{"id": row[0], "revision": row[1], "status": row[2], "created_at": row[3]} for row in rows])
    if path == "/api/events" and method == "POST":
        try:
            length = min(int(environ.get("CONTENT_LENGTH") or "0"), 8192)
            value = json.loads(environ["wsgi.input"].read(length))
            revision, status = value["revision"], value["status"]
            if not isinstance(revision, str) or not revision or len(revision) > 100 or status not in {"healthy", "degraded", "failed"}:
                raise ValueError
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return _json(start_response, "400 Bad Request", {"error": "invalid_event"})
        with _connection() as connection:
            cursor = connection.execute("INSERT INTO events(revision, status) VALUES (?, ?)", (revision, status))
            connection.commit()
        return _json(start_response, "201 Created", {"id": cursor.lastrowid, "revision": revision, "status": status})
    target = STATIC / ("index.html" if path == "/" else path.lstrip("/"))
    if target.is_file() and STATIC in target.resolve().parents:
        raw = target.read_bytes()
        content_type = "text/html; charset=utf-8" if target.suffix == ".html" else "text/css" if target.suffix == ".css" else "text/javascript"
        start_response("200 OK", [("Content-Type", content_type), ("Content-Length", str(len(raw)))])
        return [raw]
    return _json(start_response, "404 Not Found", {"error": "not_found"})


if __name__ == "__main__":
    with make_server("0.0.0.0", int(os.getenv("PORT", "8080")), application) as server:
        server.serve_forever()
"""

_INDEX = """<!doctype html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Deployment board</title><link rel="stylesheet" href="/app.css"></head>
<body><main><p class="eyebrow">BARISTA CONTROL ROOM</p><header><div><h1>Deployment board</h1><p>One container. Live health. Durable history.</p></div><div id="health" class="pill">Checking…</div></header><section class="grid"><article><span>Current revision</span><strong id="revision">—</strong></article><article><span>Service state</span><strong id="state">—</strong></article><article><span>Recorded events</span><strong id="count">0</strong></article></section><section class="ledger"><div><h2>Deployment history</h2><button id="record">Record healthy deploy</button></div><table><thead><tr><th>Revision</th><th>Status</th><th>Recorded</th></tr></thead><tbody id="events"></tbody></table></section></main><script type="module" src="/app.js"></script></body></html>
"""

_CSS = """*{box-sizing:border-box}body{margin:0;background:#0d1117;color:#e6edf3;font:16px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}main{max-width:1100px;margin:auto;padding:64px 24px}.eyebrow{color:#7ee787;letter-spacing:.15em}header,.ledger>div{display:flex;justify-content:space-between;align-items:end;gap:24px}h1{font:700 clamp(2.5rem,7vw,5rem)/.95 system-ui;margin:.2em 0}.pill{border:1px solid #30363d;border-radius:99px;padding:10px 16px}.pill.ok{color:#7ee787;border-color:#238636}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin:48px 0}.grid article,.ledger{background:#161b22;border:1px solid #30363d;border-radius:16px;padding:24px}.grid span{display:block;color:#8b949e;margin-bottom:12px}.grid strong{font-size:1.35rem}button{background:#238636;color:white;border:0;border-radius:8px;padding:11px 16px;font:inherit;cursor:pointer}table{width:100%;border-collapse:collapse;margin-top:20px}th,td{text-align:left;border-top:1px solid #30363d;padding:14px 8px}th{color:#8b949e}@media(max-width:700px){main{padding-top:32px}header,.ledger>div{align-items:start;flex-direction:column}.grid{grid-template-columns:1fr}table{font-size:.8rem}}
"""

_JS = """const health=document.querySelector("#health"),revision=document.querySelector("#revision"),state=document.querySelector("#state"),count=document.querySelector("#count"),events=document.querySelector("#events");async function refresh(){const h=await fetch("/api/health").then(r=>r.json()),rows=await fetch("/api/events").then(r=>r.json());health.textContent=h.status;health.className="pill ok";revision.textContent=h.revision;state.textContent=h.status;count.textContent=rows.length;events.innerHTML=rows.map(row=>`<tr><td>${row.revision}</td><td>${row.status}</td><td>${row.created_at}</td></tr>`).join("")||'<tr><td colspan="3">No deployments recorded yet.</td></tr>'}document.querySelector("#record").addEventListener("click",async()=>{await fetch("/api/events",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({revision:revision.textContent,status:"healthy"})});refresh()});refresh();
"""


def implement_feature(document: Mapping[str, Any], root: Path) -> str:
    number, title, body, _ = _issue(document)
    uri = _issue_uri(number)
    marker = _FEATURE_MARKER.search(body)
    if marker is None:
        raise ObjectiveError("canonical feature marker is absent")
    _, feature, _ = marker.groups()
    _write_issue_record(root, number, title, body, uri)
    (root / "app").mkdir(exist_ok=True)
    (root / "app" / "__init__.py").write_text("", encoding="utf-8")
    if feature == "status-api":
        (root / "app" / "server.py").write_text(_STATUS_SERVER, encoding="utf-8")
        (root / "Dockerfile").write_text(
            'FROM python:3.12-slim\nWORKDIR /app\nCOPY app /app/app\nEXPOSE 8080\nENV BARISTA_DEMO_DB=/data/demo.sqlite3\nVOLUME ["/data"]\nCMD ["python", "-m", "app.server"]\n',
            encoding="utf-8",
        )
        (root / "product-manifest.json").write_bytes(
            _canonical(
                {
                    "schema_version": "v1alpha1",
                    "name": "deployment-board",
                    "runtime": {"containers": 1, "port": 8080},
                    "bindings": {
                        "state": {
                            "path": "/data",
                            "writable": True,
                            "kind": "sqlite-state",
                        }
                    },
                }
            )
        )
    elif feature == "event-store":
        if not (root / "app" / "server.py").exists():
            raise ObjectiveError("status-api dependency is absent")
        (root / "app" / "server.py").write_text(_EVENT_SERVER, encoding="utf-8")
        (root / "tests").mkdir(exist_ok=True)
        (root / "tests" / "test_server.py").write_text(
            "import io,json,os,tempfile,unittest\nfrom pathlib import Path\nclass ServerTest(unittest.TestCase):\n def test_health_and_events(self):\n  with tempfile.TemporaryDirectory() as d:\n   os.environ['BARISTA_DEMO_DB']=str(Path(d)/'events.sqlite3')\n   import importlib,app.server as server; importlib.reload(server)\n   def call(path,method='GET',body=b''):\n    status=[]\n    result=server.application({'PATH_INFO':path,'REQUEST_METHOD':method,'CONTENT_LENGTH':str(len(body)),'wsgi.input':io.BytesIO(body)},lambda value,_:status.append(value))\n    return status[0],json.loads(b''.join(result))\n   self.assertEqual(call('/api/health')[0],'200 OK')\n   self.assertEqual(call('/api/events','POST',b'{\"revision\":\"abc\",\"status\":\"healthy\"}')[0],'201 Created')\n   self.assertEqual(len(call('/api/events')[1]),1)\n",
            encoding="utf-8",
        )
    elif feature == "dashboard":
        if "api/events" not in (root / "app" / "server.py").read_text():
            raise ObjectiveError("event-store dependency is absent")
        source = root / "web" / "src"
        source.mkdir(parents=True, exist_ok=True)
        (source / "index.html").write_text(_INDEX, encoding="utf-8")
        (source / "app.css").write_text(_CSS, encoding="utf-8")
        (source / "app.js").write_text(_JS, encoding="utf-8")
        compiled = root / "web" / "dist"
        compiled.mkdir(parents=True, exist_ok=True)
        for name in ("index.html", "app.css", "app.js"):
            (compiled / name).write_bytes((source / name).read_bytes())
        (root / "web" / "build.mjs").write_text(
            "import{cp,mkdir,rm}from'node:fs/promises';await rm(new URL('./dist',import.meta.url),{recursive:true,force:true});await mkdir(new URL('./dist',import.meta.url));await cp(new URL('./src',import.meta.url),new URL('./dist',import.meta.url),{recursive:true});\n",
            encoding="utf-8",
        )
        (root / "Dockerfile").write_text(
            'FROM node:22-alpine AS frontend\nWORKDIR /src/web\nCOPY web /src/web\nRUN node build.mjs\n\nFROM python:3.12-slim\nWORKDIR /app\nCOPY app /app/app\nCOPY --from=frontend /src/web/dist /app/web/dist\nEXPOSE 8080\nENV BARISTA_DEMO_DB=/data/demo.sqlite3\nVOLUME ["/data"]\nCMD ["python", "-m", "app.server"]\n',
            encoding="utf-8",
        )
    else:
        raise ObjectiveError("feature identity is unsupported")
    return feature


def brd_main() -> int:
    try:
        document = _read("BARISTA_OBJECTIVE_PATH")
        target = author_brd(document, _workspace())
    except (OSError, ObjectiveError) as exc:
        raise SystemExit(f"BRD objective error: {exc}") from exc
    print(json.dumps({"brd": str(target)}, sort_keys=True))
    return 0


def planner_main() -> int:
    output = os.environ.get("BARISTA_FEATURE_PLAN_PATH")
    if not output:
        raise SystemExit("BARISTA_FEATURE_PLAN_PATH is required")
    try:
        document = _read("BARISTA_PROGRAM_OBJECTIVE_PATH")
        plan = plan_features(document, _workspace())
        Path(output).write_bytes(_canonical(plan))
    except (OSError, ObjectiveError) as exc:
        raise SystemExit(f"planning objective error: {exc}") from exc
    return 0


def feature_main() -> int:
    try:
        document = _read("BARISTA_OBJECTIVE_PATH")
        feature = implement_feature(document, _workspace())
    except (OSError, ObjectiveError) as exc:
        raise SystemExit(f"feature objective error: {exc}") from exc
    print(json.dumps({"feature": feature}, sort_keys=True))
    return 0
