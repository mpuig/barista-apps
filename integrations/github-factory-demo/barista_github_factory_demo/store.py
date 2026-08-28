"""Small durable webhook claim and result store."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Claim:
    delivery_id: str
    repository: str
    issue_number: int
    issue_uri: str
    status: str
    run_name: str
    created: bool = False


class DeliveryStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS deliveries (
                delivery_id TEXT PRIMARY KEY,
                repository TEXT NOT NULL,
                issue_number INTEGER NOT NULL,
                issue_uri TEXT NOT NULL,
                status TEXT NOT NULL,
                run_name TEXT NOT NULL,
                result_json TEXT,
                error TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                UNIQUE(repository, issue_number)
            );
            """
        )
        self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def claim(
        self,
        *,
        delivery_id: str,
        repository: str,
        issue_number: int,
        issue_uri: str,
        run_name: str,
    ) -> Claim:
        now = int(time.time())
        with self._lock:
            try:
                self._connection.execute(
                    """INSERT INTO deliveries
                    (delivery_id, repository, issue_number, issue_uri, status, run_name, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 'accepted', ?, ?, ?)""",
                    (
                        delivery_id,
                        repository,
                        issue_number,
                        issue_uri,
                        run_name,
                        now,
                        now,
                    ),
                )
                self._connection.commit()
                return Claim(
                    delivery_id,
                    repository,
                    issue_number,
                    issue_uri,
                    "accepted",
                    run_name,
                    True,
                )
            except sqlite3.IntegrityError:
                row = self._connection.execute(
                    """SELECT * FROM deliveries
                    WHERE delivery_id = ? OR (repository = ? AND issue_number = ?)
                    ORDER BY delivery_id = ? DESC LIMIT 1""",
                    (delivery_id, repository, issue_number, delivery_id),
                ).fetchone()
                if row is None:  # pragma: no cover - transaction invariant
                    raise
                return self._claim(row, created=False)

    def mark_running(self, delivery_id: str) -> None:
        self._update(delivery_id, status="running", error=None)

    def succeed(self, delivery_id: str, result: dict) -> None:
        self._update(
            delivery_id,
            status="succeeded",
            result_json=json.dumps(result, sort_keys=True, separators=(",", ":")),
            error=None,
        )

    def fail(self, delivery_id: str, message: str) -> None:
        # Persist useful category/prose without allowing an unbounded provider
        # response or accidental credential dump into status surfaces.
        sanitized = " ".join(str(message).split())[:1000]
        self._update(delivery_id, status="failed", error=sanitized)

    def _update(self, delivery_id: str, **values: Any) -> None:
        values["updated_at"] = int(time.time())
        assignments = ", ".join(f"{column} = ?" for column in values)
        with self._lock:
            cursor = self._connection.execute(
                f"UPDATE deliveries SET {assignments} WHERE delivery_id = ?",
                (*values.values(), delivery_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(delivery_id)
            self._connection.commit()

    def get(self, delivery_id: str) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM deliveries WHERE delivery_id = ?", (delivery_id,)
            ).fetchone()
        return self._document(row) if row is not None else None

    def get_issue(self, repository: str, issue_number: int) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM deliveries WHERE repository = ? AND issue_number = ?",
                (repository, issue_number),
            ).fetchone()
        return self._document(row) if row is not None else None

    def recoverable(self) -> list[Claim]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM deliveries WHERE status IN ('accepted', 'running') ORDER BY created_at"
            ).fetchall()
        return [self._claim(row, created=False) for row in rows]

    @staticmethod
    def _claim(row: sqlite3.Row, *, created: bool) -> Claim:
        return Claim(
            delivery_id=row["delivery_id"],
            repository=row["repository"],
            issue_number=int(row["issue_number"]),
            issue_uri=row["issue_uri"],
            status=row["status"],
            run_name=row["run_name"],
            created=created,
        )

    @staticmethod
    def _document(row: sqlite3.Row) -> dict:
        result = json.loads(row["result_json"]) if row["result_json"] else None
        return {
            "delivery_id": row["delivery_id"],
            "repository": row["repository"],
            "issue_number": int(row["issue_number"]),
            "issue_uri": row["issue_uri"],
            "status": row["status"],
            "run_name": row["run_name"],
            "result": result,
            "error": row["error"],
            "created_at": int(row["created_at"]),
            "updated_at": int(row["updated_at"]),
        }
