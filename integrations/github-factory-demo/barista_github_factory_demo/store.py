"""Small durable webhook claim and result store."""

from __future__ import annotations

import json
import re
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
    attempt: int = 1
    answer_comment_id: int | None = None
    answer: str | None = None
    prior_result_digest: str | None = None
    answers: tuple[dict, ...] = ()


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
                attempt INTEGER NOT NULL DEFAULT 1,
                answer_comment_id INTEGER,
                answer TEXT,
                prior_result_digest TEXT,
                question_digest TEXT,
                answer_history_json TEXT,
                UNIQUE(repository, issue_number)
            );
            CREATE TABLE IF NOT EXISTS comment_deliveries (
                delivery_id TEXT PRIMARY KEY,
                repository TEXT NOT NULL,
                issue_number INTEGER NOT NULL,
                comment_id INTEGER NOT NULL UNIQUE,
                disposition TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS project_projections (
                issue_uri TEXT PRIMARY KEY,
                desired_status TEXT NOT NULL,
                projected_status TEXT,
                item_id TEXT,
                last_error TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL
            );
            """
        )
        columns = {
            str(row[1])
            for row in self._connection.execute("PRAGMA table_info(deliveries)")
        }
        for name, declaration in (
            ("attempt", "INTEGER NOT NULL DEFAULT 1"),
            ("answer_comment_id", "INTEGER"),
            ("answer", "TEXT"),
            ("prior_result_digest", "TEXT"),
            ("question_digest", "TEXT"),
            ("answer_history_json", "TEXT"),
        ):
            if name not in columns:
                self._connection.execute(
                    f"ALTER TABLE deliveries ADD COLUMN {name} {declaration}"
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
                    delivery_id=delivery_id,
                    repository=repository,
                    issue_number=issue_number,
                    issue_uri=issue_uri,
                    status="accepted",
                    run_name=run_name,
                    created=True,
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

    def refuse(self, delivery_id: str, result: dict) -> None:
        self._update(
            delivery_id,
            status="refused",
            result_json=json.dumps(result, sort_keys=True, separators=(",", ":")),
            error=None,
        )

    def await_input(self, delivery_id: str, result: dict) -> None:
        digest = result.get("question_digest")
        if (
            not isinstance(digest, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None
        ):
            raise ValueError("needs_input result has no question digest")
        self._update(
            delivery_id,
            status="awaiting_input",
            result_json=json.dumps(result, sort_keys=True, separators=(",", ":")),
            question_digest=digest,
            error=None,
        )

    def accept_answer(
        self,
        *,
        delivery_id: str,
        repository: str,
        issue_number: int,
        comment_id: int,
        answer: str,
        run_name_prefix: str,
    ) -> tuple[Claim | None, str]:
        """Atomically deduplicate a comment and advance only awaiting work."""
        now = int(time.time())
        with self._lock:
            try:
                self._connection.execute(
                    """INSERT INTO comment_deliveries
                    (delivery_id, repository, issue_number, comment_id, disposition, created_at)
                    VALUES (?, ?, ?, ?, 'received', ?)""",
                    (delivery_id, repository, issue_number, comment_id, now),
                )
            except sqlite3.IntegrityError:
                self._connection.rollback()
                return None, "duplicate"
            row = self._connection.execute(
                "SELECT * FROM deliveries WHERE repository = ? AND issue_number = ?",
                (repository, issue_number),
            ).fetchone()
            if row is None or row["status"] != "awaiting_input":
                self._connection.execute(
                    "UPDATE comment_deliveries SET disposition = 'stale' WHERE delivery_id = ?",
                    (delivery_id,),
                )
                self._connection.commit()
                return None, "stale"
            attempt = int(row["attempt"]) + 1
            run_name = f"{run_name_prefix}-attempt-{attempt}"
            prior_result = json.loads(row["result_json"]) if row["result_json"] else {}
            prior = prior_result.get("factory_result_digest")
            history = (
                json.loads(row["answer_history_json"])
                if row["answer_history_json"]
                else []
            )
            if len(history) >= 20 or attempt > 100:
                self._connection.execute(
                    "UPDATE comment_deliveries SET disposition = 'answer_limit' WHERE delivery_id = ?",
                    (delivery_id,),
                )
                self._connection.commit()
                return None, "answer_limit"
            history.append(
                {
                    "comment_id": comment_id,
                    "body": answer,
                    **({"prior_result_digest": prior} if prior else {}),
                }
            )
            history_json = json.dumps(history, sort_keys=True, separators=(",", ":"))
            self._connection.execute(
                """UPDATE deliveries SET status = 'accepted', run_name = ?, attempt = ?,
                answer_comment_id = ?, answer = ?, prior_result_digest = ?,
                answer_history_json = ?, updated_at = ? WHERE delivery_id = ?""",
                (
                    run_name,
                    attempt,
                    comment_id,
                    answer,
                    prior,
                    history_json,
                    now,
                    row["delivery_id"],
                ),
            )
            self._connection.execute(
                "UPDATE comment_deliveries SET disposition = 'accepted' WHERE delivery_id = ?",
                (delivery_id,),
            )
            self._connection.commit()
            updated = self._connection.execute(
                "SELECT * FROM deliveries WHERE delivery_id = ?", (row["delivery_id"],)
            ).fetchone()
            return self._claim(updated, created=True), "accepted"

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
            projection = (
                self._projection_row(row["issue_uri"]) if row is not None else None
            )
        return self._document(row, projection) if row is not None else None

    def get_issue(self, repository: str, issue_number: int) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM deliveries WHERE repository = ? AND issue_number = ?",
                (repository, issue_number),
            ).fetchone()
            projection = (
                self._projection_row(row["issue_uri"]) if row is not None else None
            )
        return self._document(row, projection) if row is not None else None

    def desire_projection(self, issue_uri: str, status: str) -> None:
        """Durably record a projection target after canonical state commits."""
        now = int(time.time())
        with self._lock:
            self._connection.execute(
                """INSERT INTO project_projections
                (issue_uri, desired_status, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(issue_uri) DO UPDATE SET
                    desired_status = excluded.desired_status,
                    updated_at = excluded.updated_at""",
                (issue_uri, status, now),
            )
            self._connection.commit()

    def projection_targets(self) -> list[tuple[str, str]]:
        """Return canonical delivery states for startup reconciliation."""
        with self._lock:
            rows = self._connection.execute(
                "SELECT issue_uri, status FROM deliveries ORDER BY created_at"
            ).fetchall()
        return [(str(row["issue_uri"]), str(row["status"])) for row in rows]

    def projection_succeeded(self, issue_uri: str, status: str, item_id: str) -> None:
        now = int(time.time())
        with self._lock:
            self._connection.execute(
                """UPDATE project_projections SET projected_status = ?, item_id = ?,
                last_error = NULL, attempts = attempts + 1, updated_at = ?
                WHERE issue_uri = ? AND desired_status = ?""",
                (status, item_id, now, issue_uri, status),
            )
            self._connection.commit()

    def projection_failed(self, issue_uri: str, status: str, message: str) -> None:
        sanitized = " ".join(str(message).split())[:500]
        now = int(time.time())
        with self._lock:
            self._connection.execute(
                """UPDATE project_projections SET last_error = ?,
                attempts = attempts + 1, updated_at = ?
                WHERE issue_uri = ? AND desired_status = ?""",
                (sanitized, now, issue_uri, status),
            )
            self._connection.commit()

    def _projection_row(self, issue_uri: str) -> sqlite3.Row | None:
        return self._connection.execute(
            "SELECT * FROM project_projections WHERE issue_uri = ?", (issue_uri,)
        ).fetchone()

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
            attempt=int(row["attempt"]),
            answer_comment_id=row["answer_comment_id"],
            answer=row["answer"],
            prior_result_digest=row["prior_result_digest"],
            answers=tuple(
                json.loads(row["answer_history_json"])
                if row["answer_history_json"]
                else []
            ),
            created=created,
        )

    @staticmethod
    def _document(row: sqlite3.Row, projection: sqlite3.Row | None = None) -> dict:
        result = json.loads(row["result_json"]) if row["result_json"] else None
        projection_document = None
        if projection is not None:
            projection_document = {
                "desired_status": projection["desired_status"],
                "projected_status": projection["projected_status"],
                "item_id": projection["item_id"],
                "last_error": projection["last_error"],
                "attempts": int(projection["attempts"]),
                "updated_at": int(projection["updated_at"]),
            }
        return {
            "delivery_id": row["delivery_id"],
            "repository": row["repository"],
            "issue_number": int(row["issue_number"]),
            "issue_uri": row["issue_uri"],
            "status": row["status"],
            "run_name": row["run_name"],
            "attempt": int(row["attempt"]),
            "answer_comment_id": row["answer_comment_id"],
            "answer_count": len(
                json.loads(row["answer_history_json"])
                if row["answer_history_json"]
                else []
            ),
            "prior_result_digest": row["prior_result_digest"],
            "question_digest": row["question_digest"],
            "result": result,
            "error": row["error"],
            "created_at": int(row["created_at"]),
            "updated_at": int(row["updated_at"]),
            "project_projection": projection_document,
        }
