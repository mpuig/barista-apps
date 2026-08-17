"""Local durable metadata + artifact storage.

Provider truth lives here, not in the node: installed apps, sessions and their
node instance ids, idempotency keys, an append-only event journal with stable
cursors, operations, and content-addressed artifacts. SQLite + files, so it is
documented, exportable, and survives a provider restart with the same logical
identifiers. Nothing here is proprietary or Cloud-specific.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS apps (
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    digest TEXT NOT NULL,
    manifest TEXT NOT NULL,
    granted_capabilities TEXT NOT NULL,
    installed_at TEXT NOT NULL,
    PRIMARY KEY (name, version)
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    name TEXT,
    app TEXT NOT NULL,
    node_instance_id TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    parent_session_id TEXT,
    metadata TEXT
);
CREATE TABLE IF NOT EXISTS idempotency (
    key TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS operations (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    done INTEGER NOT NULL,
    session_id TEXT,
    result TEXT,
    error TEXT,
    last_event_cursor TEXT
);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    type TEXT NOT NULL,
    operation_id TEXT,
    time TEXT NOT NULL,
    data TEXT
);
CREATE INDEX IF NOT EXISTS events_by_session ON events(session_id, seq);
CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    name TEXT NOT NULL,
    digest TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    media_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    metadata TEXT
);
"""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def cursor_of(seq: int) -> str:
    return f"{seq:012d}"


class Store:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "provider.db"
        self._db = sqlite3.connect(self.db_path, check_same_thread=False)
        # WAL lets a status poll (read) proceed without blocking on a writer, so
        # the hot read path is not serialized behind lifecycle writes.
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    @property
    def node_state_path(self) -> Path:
        return self.data_dir / "node-state.json"

    # -- apps ------------------------------------------------------------- #
    def install_app(self, manifest: dict, granted: list[str]) -> dict:
        rec = {
            "name": manifest["name"],
            "version": manifest["version"],
            "digest": manifest["workload"]["digest"],
            "granted_capabilities": granted,
            "installed_at": _now(),
        }
        self._db.execute(
            "INSERT OR REPLACE INTO apps VALUES (?,?,?,?,?,?)",
            (rec["name"], rec["version"], rec["digest"], json.dumps(manifest),
             json.dumps(granted), rec["installed_at"]),
        )
        self._db.commit()
        return rec

    def get_app(self, name: str) -> Optional[dict]:
        row = self._db.execute(
            "SELECT * FROM apps WHERE name=? ORDER BY installed_at DESC LIMIT 1", (name,)
        ).fetchone()
        return dict(row) if row else None

    # -- idempotency ------------------------------------------------------ #
    def idempotent_lookup(self, key: Optional[str], kind: str) -> Optional[str]:
        if not key:
            return None
        row = self._db.execute(
            "SELECT resource_id FROM idempotency WHERE key=? AND kind=?", (key, kind)
        ).fetchone()
        return row["resource_id"] if row else None

    def idempotent_record(self, key: Optional[str], kind: str, resource_id: str) -> None:
        if not key:
            return
        # REPLACE, not IGNORE: if a key's prior resource is gone (e.g. the session
        # it minted was deleted), the mapping is refreshed to the new resource so
        # a later replay stays idempotent instead of minting a fresh one each time.
        self._db.execute(
            "INSERT OR REPLACE INTO idempotency VALUES (?,?,?,?)",
            (key, kind, resource_id, _now()),
        )
        self._db.commit()

    def purge_idempotency_for(self, resource_id: str) -> None:
        """Drop idempotency rows pointing at a resource that no longer exists."""
        self._db.execute("DELETE FROM idempotency WHERE resource_id=?", (resource_id,))
        self._db.commit()

    # -- sessions --------------------------------------------------------- #
    def create_session(
        self,
        node_instance_id: str,
        app: str,
        name: Optional[str],
        metadata: Optional[dict],
        parent_session_id: Optional[str] = None,
    ) -> dict:
        sid = "sess-" + uuid.uuid4().hex[:16]
        created = _now()
        self._db.execute(
            "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?)",
            (sid, name, app, node_instance_id, "running", created,
             parent_session_id, json.dumps(metadata or {})),
        )
        self._db.commit()
        return self.get_session(sid)

    def get_session(self, sid: str) -> Optional[dict]:
        row = self._db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        if not row:
            return None
        return self._session_dict(row)

    def list_sessions(self, app: Optional[str] = None) -> list[dict]:
        if app:
            rows = self._db.execute("SELECT * FROM sessions WHERE app=? ORDER BY created_at", (app,)).fetchall()
        else:
            rows = self._db.execute("SELECT * FROM sessions ORDER BY created_at").fetchall()
        return [self._session_dict(r) for r in rows]

    def set_session_state(self, sid: str, state: str) -> None:
        self._db.execute("UPDATE sessions SET state=? WHERE id=?", (state, sid))
        self._db.commit()

    def delete_session(self, sid: str) -> None:
        self._db.execute("DELETE FROM sessions WHERE id=?", (sid,))
        # The ensure key that minted this session must not resurrect a ghost.
        self._db.execute("DELETE FROM idempotency WHERE resource_id=?", (sid,))
        self._db.commit()

    def node_instance_id(self, sid: str) -> Optional[str]:
        row = self._db.execute("SELECT node_instance_id FROM sessions WHERE id=?", (sid,)).fetchone()
        return row["node_instance_id"] if row else None

    def _session_dict(self, row: sqlite3.Row) -> dict:
        out = {
            "id": row["id"],
            "app": row["app"],
            "state": row["state"],
            "created_at": row["created_at"],
            "metadata": json.loads(row["metadata"] or "{}"),
        }
        # `name` is an optional string in the Host API schema (no null in
        # OpenAPI 3.1): omit it rather than emit null so a schema-validating
        # client accepts an unnamed session.
        if row["name"]:
            out["name"] = row["name"]
        if row["parent_session_id"]:
            out["lineage"] = {"parent_session_id": row["parent_session_id"]}
        return out

    # -- operations ------------------------------------------------------- #
    def create_operation(self, kind: str, session_id: Optional[str], done: bool,
                         result: Optional[dict] = None, last_cursor: Optional[str] = None) -> dict:
        op_id = "op-" + uuid.uuid4().hex[:16]
        self._db.execute(
            "INSERT INTO operations VALUES (?,?,?,?,?,?,?)",
            (op_id, kind, 1 if done else 0, session_id,
             json.dumps(result) if result is not None else None, None, last_cursor),
        )
        self._db.commit()
        return self.get_operation(op_id)

    def get_operation(self, op_id: str) -> Optional[dict]:
        row = self._db.execute("SELECT * FROM operations WHERE id=?", (op_id,)).fetchone()
        if not row:
            return None
        out = {"id": row["id"], "kind": row["kind"], "done": bool(row["done"])}
        if row["session_id"]:
            out["session_id"] = row["session_id"]
        if row["result"]:
            out["result"] = json.loads(row["result"])
        if row["error"]:
            out["error"] = json.loads(row["error"])
        if row["last_event_cursor"]:
            out["last_event_cursor"] = row["last_event_cursor"]
        return out

    # -- events ----------------------------------------------------------- #
    def append_event(self, session_id: str, type_: str, data: dict,
                    operation_id: Optional[str] = None) -> str:
        cur = self._db.execute(
            "INSERT INTO events (session_id, type, operation_id, time, data) VALUES (?,?,?,?,?)",
            (session_id, type_, operation_id, _now(), json.dumps(data)),
        )
        self._db.commit()
        return cursor_of(cur.lastrowid)

    def current_max_cursor(self, session_id: str) -> str:
        """The newest cursor for a session, as an exclusive resume point. Reading
        events after it yields only what is appended next — which is exactly the
        cursor an exec should hand back so the caller sees the command's output."""
        row = self._db.execute(
            "SELECT MAX(seq) AS m FROM events WHERE session_id=?", (session_id,)
        ).fetchone()
        return cursor_of(row["m"] or 0)

    def read_events(self, session_id: str, after_cursor: Optional[str] = None,
                   limit: int = 100) -> list[dict]:
        after = int(after_cursor) if after_cursor else 0
        rows = self._db.execute(
            "SELECT * FROM events WHERE session_id=? AND seq>? ORDER BY seq LIMIT ?",
            (session_id, after, limit),
        ).fetchall()
        events = []
        for r in rows:
            ev = {
                "cursor": cursor_of(r["seq"]),
                "type": r["type"],
                "session_id": r["session_id"],
                "time": r["time"],
                "data": json.loads(r["data"] or "{}"),
            }
            if r["operation_id"]:
                ev["operation_id"] = r["operation_id"]
            events.append(ev)
        return events

    # -- artifacts -------------------------------------------------------- #
    def register_artifact(self, session_id: str, body: dict) -> dict:
        art_id = "art-" + uuid.uuid4().hex[:16]
        created = _now()
        self._db.execute(
            "INSERT INTO artifacts VALUES (?,?,?,?,?,?,?,?)",
            (art_id, session_id, body["name"], body["digest"], body["size_bytes"],
             body["media_type"], created, json.dumps(body.get("metadata") or {})),
        )
        self._db.commit()
        return {
            "id": art_id, "name": body["name"], "digest": body["digest"],
            "size_bytes": body["size_bytes"], "media_type": body["media_type"],
            "created_at": created,
        }

    def get_artifact(self, artifact_id: str) -> Optional[dict]:
        r = self._db.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        if not r:
            return None
        return {
            "id": r["id"], "name": r["name"], "digest": r["digest"],
            "size_bytes": r["size_bytes"], "media_type": r["media_type"],
            "created_at": r["created_at"],
        }

    def list_artifacts(self, session_id: str) -> list[dict]:
        rows = self._db.execute(
            "SELECT * FROM artifacts WHERE session_id=? ORDER BY created_at", (session_id,)
        ).fetchall()
        return [
            {
                "id": r["id"], "name": r["name"], "digest": r["digest"],
                "size_bytes": r["size_bytes"], "media_type": r["media_type"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]
