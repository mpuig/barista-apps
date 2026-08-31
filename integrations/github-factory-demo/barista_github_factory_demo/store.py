"""Small durable webhook claim and result store."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
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
    workflow_kind: str = "issue"
    program_id: str | None = None
    feature_id: str | None = None


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
                workflow_kind TEXT NOT NULL DEFAULT 'issue',
                program_id TEXT,
                feature_id TEXT,
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
            CREATE TABLE IF NOT EXISTS programs (
                program_id TEXT PRIMARY KEY,
                repository TEXT NOT NULL,
                issue_number INTEGER NOT NULL,
                issue_uri TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                brd_delivery_id TEXT NOT NULL,
                brd_pr_number INTEGER,
                brd_pr_uri TEXT,
                brd_head_commit TEXT,
                brd_path TEXT,
                brd_digest TEXT,
                approved_commit TEXT,
                approved_by TEXT,
                approved_at INTEGER,
                plan_json TEXT,
                plan_digest TEXT,
                acceptance_json TEXT,
                error TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS program_features (
                program_id TEXT NOT NULL,
                feature_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                criteria_json TEXT NOT NULL,
                dependencies_json TEXT NOT NULL,
                status TEXT NOT NULL,
                issue_number INTEGER,
                issue_uri TEXT,
                delivery_id TEXT,
                pr_number INTEGER,
                pr_uri TEXT,
                head_commit TEXT,
                merged_commit TEXT,
                error TEXT,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY(program_id, feature_id),
                UNIQUE(issue_uri),
                UNIQUE(pr_uri)
            );
            CREATE TABLE IF NOT EXISTS external_deliveries (
                delivery_id TEXT PRIMARY KEY,
                event TEXT NOT NULL,
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
                details_json TEXT,
                updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS activity_projections (
                program_id TEXT PRIMARY KEY,
                revision INTEGER NOT NULL,
                content_digest TEXT NOT NULL,
                document_json TEXT NOT NULL,
                published_digest TEXT,
                last_error TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS program_events (
                program_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT,
                phase TEXT,
                actor TEXT,
                links_json TEXT NOT NULL,
                attributes_json TEXT NOT NULL,
                occurred_at INTEGER NOT NULL,
                PRIMARY KEY(program_id, event_id),
                FOREIGN KEY(program_id) REFERENCES programs(program_id)
            );
            CREATE INDEX IF NOT EXISTS ix_program_events_order
                ON program_events(program_id, occurred_at, event_id);
            CREATE TABLE IF NOT EXISTS deployment_requests (
                request_id TEXT PRIMARY KEY,
                program_id TEXT NOT NULL,
                state TEXT NOT NULL,
                result_json TEXT,
                error TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                FOREIGN KEY(program_id) REFERENCES programs(program_id)
            );
            CREATE TABLE IF NOT EXISTS demo_scenarios (
                idempotency_key TEXT PRIMARY KEY,
                scenario_id TEXT NOT NULL,
                issue_number INTEGER NOT NULL UNIQUE,
                issue_uri TEXT NOT NULL UNIQUE,
                created_at INTEGER NOT NULL,
                reset_at INTEGER
            );
            CREATE INDEX IF NOT EXISTS ix_demo_scenarios_recent
                ON demo_scenarios(created_at DESC);
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
            ("workflow_kind", "TEXT NOT NULL DEFAULT 'issue'"),
            ("program_id", "TEXT"),
            ("feature_id", "TEXT"),
        ):
            if name not in columns:
                self._connection.execute(
                    f"ALTER TABLE deliveries ADD COLUMN {name} {declaration}"
                )
        projection_columns = {
            str(row[1])
            for row in self._connection.execute(
                "PRAGMA table_info(project_projections)"
            )
        }
        if "details_json" not in projection_columns:
            self._connection.execute(
                "ALTER TABLE project_projections ADD COLUMN details_json TEXT"
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
        workflow_kind: str = "issue",
        program_id: str | None = None,
        feature_id: str | None = None,
    ) -> Claim:
        now = int(time.time())
        with self._lock:
            try:
                self._connection.execute(
                    """INSERT INTO deliveries
                    (delivery_id, repository, issue_number, issue_uri, status, run_name,
                     created_at, updated_at, workflow_kind, program_id, feature_id)
                    VALUES (?, ?, ?, ?, 'accepted', ?, ?, ?, ?, ?, ?)""",
                    (
                        delivery_id,
                        repository,
                        issue_number,
                        issue_uri,
                        run_name,
                        now,
                        now,
                        workflow_kind,
                        program_id,
                        feature_id,
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
                    workflow_kind=workflow_kind,
                    program_id=program_id,
                    feature_id=feature_id,
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
            if row["program_id"]:
                self.record_program_event(
                    str(row["program_id"]),
                    f"clarification-received-{attempt}",
                    "decision.received",
                    "Clarification received",
                    phase="running",
                    summary="An authorized responder supplied the missing product decision.",
                    links=[
                        {
                            "rel": "comment",
                            "label": "Clarification",
                            "url": f"{row['issue_uri']}#issuecomment-{comment_id}",
                        }
                    ],
                    occurred_at=now,
                )
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

    def desire_projection(
        self, issue_uri: str, status: str, details: dict | None = None
    ) -> None:
        """Durably record a projection target after canonical state commits."""
        now = int(time.time())
        details_json = (
            json.dumps(details, sort_keys=True, separators=(",", ":"))
            if details is not None
            else None
        )
        with self._lock:
            self._connection.execute(
                """INSERT INTO project_projections
                (issue_uri, desired_status, details_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(issue_uri) DO UPDATE SET
                    desired_status = excluded.desired_status,
                    details_json = COALESCE(excluded.details_json, project_projections.details_json),
                    updated_at = excluded.updated_at""",
                (issue_uri, status, details_json, now),
            )
            self._connection.commit()

    def projection_targets(self) -> list[tuple[str, str, dict | None]]:
        """Return durable desired fields for startup reconciliation."""
        with self._lock:
            deliveries = self._connection.execute(
                "SELECT issue_uri, status FROM deliveries ORDER BY created_at"
            ).fetchall()
            for row in deliveries:
                self._connection.execute(
                    """INSERT INTO project_projections
                    (issue_uri, desired_status, updated_at) VALUES (?, ?, ?)
                    ON CONFLICT(issue_uri) DO NOTHING""",
                    (row["issue_uri"], row["status"], int(time.time())),
                )
            self._connection.commit()
            rows = self._connection.execute(
                "SELECT issue_uri, desired_status, details_json FROM project_projections ORDER BY updated_at"
            ).fetchall()
        return [
            (
                str(row["issue_uri"]),
                str(row["desired_status"]),
                json.loads(row["details_json"]) if row["details_json"] else None,
            )
            for row in rows
        ]

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

    def ensure_program(self, program_id: str, claim: Claim) -> None:
        now = int(time.time())
        with self._lock:
            cursor = self._connection.execute(
                """INSERT INTO programs
                (program_id, repository, issue_number, issue_uri, status,
                 brd_delivery_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'brd_running', ?, ?, ?)
                ON CONFLICT(program_id) DO NOTHING""",
                (
                    program_id,
                    claim.repository,
                    claim.issue_number,
                    claim.issue_uri,
                    claim.delivery_id,
                    now,
                    now,
                ),
            )
            created = cursor.rowcount == 1
            self._connection.commit()
        if created:
            self.record_program_event(
                program_id,
                "program-created",
                "work.created",
                "Product program opened",
                phase="queued",
                summary="The source controller accepted the root issue as a new program.",
                links=[{"rel": "issue", "label": "Root issue", "url": claim.issue_uri}],
                occurred_at=now,
            )

    def record_brd_waiting(self, program_id: str) -> None:
        self._update_program(program_id, status="brd_needs_input", error=None)
        with self._lock:
            attempt_row = self._connection.execute(
                """SELECT d.attempt FROM deliveries d JOIN programs p
                ON p.brd_delivery_id = d.delivery_id WHERE p.program_id = ?""",
                (program_id,),
            ).fetchone()
        attempt = int(attempt_row["attempt"]) if attempt_row is not None else 1
        self.record_program_event(
            program_id,
            f"clarification-requested-{attempt}",
            "decision.requested",
            "Clarification requested",
            phase="waiting",
            summary="The source ended the bounded attempt and requested a fresh human answer.",
        )

    def record_brd_running(self, program_id: str) -> None:
        self._update_program(program_id, status="brd_running", error=None)

    def record_brd_pr(self, program_id: str, result: dict) -> None:
        draft = result.get("draft") or {}
        metadata = draft.get("metadata") or {}
        uri = draft.get("uri")
        number = metadata.get("number")
        head = metadata.get("head_commit")
        if (
            not isinstance(uri, str)
            or not isinstance(number, int)
            or not isinstance(head, str)
        ):
            raise ValueError("BRD result has no correlated draft identity")
        self._update_program(
            program_id,
            status="awaiting_brd_merge",
            brd_pr_number=number,
            brd_pr_uri=uri,
            brd_head_commit=head,
            brd_path=f"docs/brd/program-{self._program_issue(program_id)}.md",
            error=None,
        )
        self.record_program_event(
            program_id,
            "brd-published",
            "proposal.published",
            "BRD published for review",
            phase="waiting",
            summary="Factory verified and published a draft; merge remained a human gate.",
            links=[{"rel": "pull-request", "label": "BRD pull request", "url": uri}],
            attributes={"head_commit": head},
        )

    def claim_external_delivery(self, delivery_id: str, event: str) -> bool:
        now = int(time.time())
        with self._lock:
            try:
                self._connection.execute(
                    """INSERT INTO external_deliveries
                    (delivery_id, event, disposition, created_at)
                    VALUES (?, ?, 'received', ?)""",
                    (delivery_id, event, now),
                )
                self._connection.commit()
                return True
            except sqlite3.IntegrityError:
                self._connection.rollback()
                return False

    def dispose_external_delivery(self, delivery_id: str, disposition: str) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE external_deliveries SET disposition = ? WHERE delivery_id = ?",
                (disposition[:80], delivery_id),
            )
            self._connection.commit()

    def pull_target(self, repository: str, number: int) -> dict | None:
        with self._lock:
            program = self._connection.execute(
                """SELECT * FROM programs WHERE repository = ?
                AND brd_pr_number = ? AND status = 'awaiting_brd_merge'""",
                (repository, number),
            ).fetchone()
            if program is not None:
                return {
                    "kind": "brd",
                    "program_id": program["program_id"],
                    "expected_uri": program["brd_pr_uri"],
                    "expected_head": program["brd_head_commit"],
                }
            feature = self._connection.execute(
                """SELECT f.*, p.repository FROM program_features f
                JOIN programs p ON p.program_id = f.program_id
                WHERE p.repository = ? AND f.pr_number = ?
                AND f.status = 'awaiting_merge'""",
                (repository, number),
            ).fetchone()
            if feature is not None:
                return {
                    "kind": "feature",
                    "program_id": feature["program_id"],
                    "feature_id": feature["feature_id"],
                    "expected_uri": feature["pr_uri"],
                    "expected_head": feature["head_commit"],
                }
        return None

    def approve_brd(
        self,
        program_id: str,
        *,
        commit: str,
        digest: str,
        actor: str,
        approved_at: int,
    ) -> None:
        self._update_program(
            program_id,
            status="planning",
            approved_commit=commit,
            brd_digest=digest,
            approved_by=actor,
            approved_at=approved_at,
            error=None,
        )
        repository = self._program_repository(program_id)
        self.record_program_event(
            program_id,
            "brd-approved",
            "decision.approved",
            "BRD approved by merge",
            phase="succeeded",
            actor=actor,
            links=[
                {
                    "rel": "commit",
                    "label": "Approved commit",
                    "url": f"{repository}/commit/{commit}",
                }
            ],
            attributes={"commit": commit, "digest": digest},
            occurred_at=approved_at,
        )

    def save_plan(self, program_id: str, plan: dict, digest: str) -> None:
        now = int(time.time())
        raw = json.dumps(plan, sort_keys=True, separators=(",", ":"))
        with self._lock:
            row = self._connection.execute(
                "SELECT plan_digest FROM programs WHERE program_id = ?", (program_id,)
            ).fetchone()
            if row is None:
                raise KeyError(program_id)
            if row["plan_digest"] is not None and row["plan_digest"] != digest:
                raise ValueError("program already has a different plan")
            self._connection.execute(
                """UPDATE programs SET status = 'publishing_features', plan_json = ?,
                plan_digest = ?, updated_at = ? WHERE program_id = ?""",
                (raw, digest, now, program_id),
            )
            for ordinal, feature in enumerate(plan["features"], 1):
                self._connection.execute(
                    """INSERT INTO program_features
                    (program_id, feature_id, ordinal, title, summary, criteria_json,
                     dependencies_json, status, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'planned', ?)
                    ON CONFLICT(program_id, feature_id) DO UPDATE SET
                      title = excluded.title, summary = excluded.summary,
                      criteria_json = excluded.criteria_json,
                      dependencies_json = excluded.dependencies_json,
                      updated_at = excluded.updated_at""",
                    (
                        program_id,
                        feature["id"],
                        ordinal,
                        feature["title"],
                        feature["summary"],
                        json.dumps(
                            feature["acceptance_criteria"], separators=(",", ":")
                        ),
                        json.dumps(feature["dependencies"], separators=(",", ":")),
                        now,
                    ),
                )
            self._connection.commit()
        self.record_program_event(
            program_id,
            "plan-validated",
            "plan.validated",
            "Dependency plan validated",
            phase="succeeded",
            summary="The source independently parsed a bounded acyclic plan.",
            attributes={"digest": digest},
            occurred_at=now,
        )

    def program_for_issue(self, issue_uri: str) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                """SELECT f.*, p.repository, p.plan_digest FROM program_features f
                JOIN programs p ON p.program_id = f.program_id
                WHERE f.issue_uri = ?""",
                (issue_uri,),
            ).fetchone()
        if row is None:
            return None
        return {
            "program_id": row["program_id"],
            "feature_id": row["feature_id"],
            "status": row["status"],
            "plan_digest": row["plan_digest"],
        }

    def assign_feature_issue(
        self, program_id: str, feature_id: str, *, number: int, uri: str
    ) -> None:
        now = int(time.time())
        with self._lock:
            cursor = self._connection.execute(
                """UPDATE program_features SET issue_number = ?, issue_uri = ?,
                status = CASE WHEN status = 'planned' THEN 'blocked' ELSE status END,
                updated_at = ? WHERE program_id = ? AND feature_id = ?
                AND (issue_uri IS NULL OR issue_uri = ?)""",
                (number, uri, now, program_id, feature_id, uri),
            )
            if cursor.rowcount != 1:
                raise KeyError((program_id, feature_id))
            self._connection.commit()
        self.record_program_event(
            program_id,
            f"feature-{feature_id}-published",
            "work.published",
            f"{feature_id} published",
            phase="queued",
            links=[{"rel": "issue", "label": "Feature issue", "url": uri}],
            attributes={"feature": feature_id},
            occurred_at=now,
        )

    def unpublished_features(self, program_id: str) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                """SELECT * FROM program_features WHERE program_id = ?
                AND issue_uri IS NULL ORDER BY ordinal""",
                (program_id,),
            ).fetchall()
        return [self._feature_document(row) for row in rows]

    def ready_features(self, program_id: str) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM program_features WHERE program_id = ? ORDER BY ordinal",
                (program_id,),
            ).fetchall()
        statuses = {str(row["feature_id"]): str(row["status"]) for row in rows}
        ready = []
        for row in rows:
            if row["status"] != "blocked" or row["issue_uri"] is None:
                continue
            dependencies = json.loads(row["dependencies_json"])
            if all(statuses.get(dependency) == "merged" for dependency in dependencies):
                ready.append(self._feature_document(row))
        return ready

    def claim_feature(self, program_id: str, feature_id: str, run_name: str) -> Claim:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM program_features WHERE program_id = ? AND feature_id = ?",
                (program_id, feature_id),
            ).fetchone()
        if row is None or row["issue_uri"] is None:
            raise KeyError((program_id, feature_id))
        delivery_id = f"program-{hashlib.sha256(program_id.encode()).hexdigest()[:16]}-{feature_id}"
        claim = self.claim(
            delivery_id=delivery_id,
            repository=self._program_repository(program_id),
            issue_number=int(row["issue_number"]),
            issue_uri=str(row["issue_uri"]),
            run_name=run_name,
            workflow_kind="feature",
            program_id=program_id,
            feature_id=feature_id,
        )
        self._update_feature(
            program_id,
            feature_id,
            status="running",
            delivery_id=claim.delivery_id,
            error=None,
        )
        return claim

    def record_feature_pr(self, program_id: str, feature_id: str, result: dict) -> None:
        draft = result.get("draft") or {}
        metadata = draft.get("metadata") or {}
        self._update_feature(
            program_id,
            feature_id,
            status="awaiting_merge",
            pr_number=int(metadata["number"]),
            pr_uri=str(draft["uri"]),
            head_commit=str(metadata["head_commit"]),
            error=None,
        )
        self._update_program(program_id, status="implementing")
        self.record_program_event(
            program_id,
            f"feature-{feature_id}-verified",
            "change.verified",
            f"{feature_id} candidate verified",
            phase="waiting",
            summary="Factory published a draft change for independent human review.",
            links=[
                {
                    "rel": "pull-request",
                    "label": "Feature pull request",
                    "url": str(draft["uri"]),
                }
            ],
            attributes={"head_commit": str(metadata["head_commit"])},
        )

    def merge_feature(self, program_id: str, feature_id: str, commit: str) -> None:
        self._update_feature(
            program_id, feature_id, status="merged", merged_commit=commit, error=None
        )
        repository = self._program_repository(program_id)
        self.record_program_event(
            program_id,
            f"feature-{feature_id}-merged",
            "change.merged",
            f"{feature_id} merged",
            phase="succeeded",
            links=[
                {
                    "rel": "commit",
                    "label": "Merged commit",
                    "url": f"{repository}/commit/{commit}",
                }
            ],
            attributes={"commit": commit},
        )

    def all_features_merged(self, program_id: str) -> bool:
        with self._lock:
            row = self._connection.execute(
                """SELECT COUNT(*) AS total,
                SUM(CASE WHEN status = 'merged' THEN 1 ELSE 0 END) AS merged
                FROM program_features WHERE program_id = ?""",
                (program_id,),
            ).fetchone()
        return bool(
            row
            and int(row["total"]) > 0
            and int(row["merged"] or 0) == int(row["total"])
        )

    def mark_implementing(self, program_id: str) -> None:
        self._update_program(program_id, status="implementing", error=None)

    def fail_feature(self, program_id: str, feature_id: str, message: str) -> None:
        self._update_feature(
            program_id,
            feature_id,
            status="failed",
            error=" ".join(str(message).split())[:1000],
        )
        self.fail_program(program_id, message)

    def recoverable_programs(self) -> list[tuple[str, str]]:
        with self._lock:
            rows = self._connection.execute(
                """SELECT program_id, status FROM programs
                WHERE status IN ('planning', 'publishing_features', 'implementing', 'accepting')
                ORDER BY created_at"""
            ).fetchall()
        return [(str(row["program_id"]), str(row["status"])) for row in rows]

    def mark_accepting(self, program_id: str) -> None:
        self._update_program(program_id, status="accepting", error=None)

    def complete_program(self, program_id: str, result: dict) -> None:
        self._update_program(
            program_id,
            status="accepted",
            acceptance_json=json.dumps(result, sort_keys=True, separators=(",", ":")),
            error=None,
        )
        commit = str(result["assembled_commit"])
        self.record_program_event(
            program_id,
            "program-accepted",
            "work.accepted",
            "Exact assembled commit accepted",
            phase="succeeded",
            summary="Authority-stripped acceptance verified the assembled product.",
            links=[
                {
                    "rel": "commit",
                    "label": "Accepted commit",
                    "url": f"{self._program_repository(program_id)}/commit/{commit}",
                }
            ],
            attributes={"commit": commit},
        )

    def fail_program(self, program_id: str, message: str) -> None:
        sanitized = " ".join(str(message).split())[:1000]
        self._update_program(program_id, status="failed", error=sanitized)
        failure_id = hashlib.sha256(sanitized.encode()).hexdigest()[:12]
        self.record_program_event(
            program_id,
            f"program-failed-{failure_id}",
            "work.failed",
            "Program failed",
            phase="failed",
            summary=sanitized,
        )

    def record_program_event(
        self,
        program_id: str,
        event_id: str,
        event_type: str,
        title: str,
        *,
        summary: str | None = None,
        phase: str | None = None,
        actor: str | None = None,
        links: list[dict] | None = None,
        attributes: dict[str, str] | None = None,
        occurred_at: int | None = None,
    ) -> None:
        """Append one source event idempotently; an identity is immutable."""
        document = (
            event_type,
            title,
            summary,
            phase,
            actor,
            json.dumps(links or [], sort_keys=True, separators=(",", ":")),
            json.dumps(attributes or {}, sort_keys=True, separators=(",", ":")),
            occurred_at or int(time.time()),
        )
        with self._lock:
            existing = self._connection.execute(
                """SELECT event_type, title, summary, phase, actor, links_json,
                attributes_json, occurred_at FROM program_events
                WHERE program_id = ? AND event_id = ?""",
                (program_id, event_id),
            ).fetchone()
            if existing is not None:
                if tuple(existing)[:-1] != document[:-1]:
                    raise ValueError(
                        "program event identity was reused with different content"
                    )
                return
            self._connection.execute(
                """INSERT INTO program_events
                (program_id, event_id, event_type, title, summary, phase, actor,
                 links_json, attributes_json, occurred_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (program_id, event_id, *document),
            )
            self._connection.commit()

    def program_events(self, program_id: str) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                """SELECT * FROM program_events WHERE program_id = ?
                ORDER BY occurred_at, event_id LIMIT 100""",
                (program_id,),
            ).fetchall()
        return [
            {
                "id": row["event_id"],
                "type": row["event_type"],
                "title": row["title"],
                **({"summary": row["summary"]} if row["summary"] else {}),
                **({"phase": row["phase"]} if row["phase"] else {}),
                "occurred_at": datetime.fromtimestamp(
                    int(row["occurred_at"]), UTC
                ).isoformat(),
                **({"actor": row["actor"]} if row["actor"] else {}),
                "links": json.loads(row["links_json"]),
                "attributes": json.loads(row["attributes_json"]),
            }
            for row in rows
        ]

    def list_programs(self) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM programs ORDER BY created_at DESC LIMIT 100"
            ).fetchall()
            result = []
            for row in rows:
                features = self._connection.execute(
                    "SELECT * FROM program_features WHERE program_id = ? ORDER BY ordinal",
                    (row["program_id"],),
                ).fetchall()
                result.append(self._program_document(row, features))
        return result

    def program_attempts(self, program_id: str) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                """SELECT delivery_id, issue_number, issue_uri, status, run_name,
                attempt, workflow_kind, feature_id, error, created_at, updated_at
                FROM deliveries WHERE program_id = ?
                ORDER BY created_at, delivery_id LIMIT 100""",
                (program_id,),
            ).fetchall()
        return [
            {
                "delivery_id": row["delivery_id"],
                "issue_number": int(row["issue_number"]),
                "issue_uri": row["issue_uri"],
                "status": row["status"],
                "run_name": row["run_name"],
                "attempt": int(row["attempt"]),
                "workflow_kind": row["workflow_kind"],
                "feature_id": row["feature_id"],
                "error": row["error"],
                "created_at": int(row["created_at"]),
                "updated_at": int(row["updated_at"]),
            }
            for row in rows
        ]

    def record_demo_scenario(
        self,
        *,
        idempotency_key: str,
        scenario_id: str,
        issue_number: int,
        issue_uri: str,
    ) -> dict:
        now = int(time.time())
        with self._lock:
            self._connection.execute(
                """INSERT INTO demo_scenarios
                (idempotency_key, scenario_id, issue_number, issue_uri, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(idempotency_key) DO NOTHING""",
                (idempotency_key, scenario_id, issue_number, issue_uri, now),
            )
            self._connection.commit()
            row = self._connection.execute(
                "SELECT * FROM demo_scenarios WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        if row is None:
            raise RuntimeError("demo scenario write was lost")
        if int(row["issue_number"]) != issue_number or row["issue_uri"] != issue_uri:
            raise ValueError("demo scenario idempotency identity conflicts")
        return self._demo_scenario_document(row)

    def current_demo_scenario(self) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                """SELECT * FROM demo_scenarios WHERE reset_at IS NULL
                ORDER BY created_at DESC LIMIT 1"""
            ).fetchone()
        return self._demo_scenario_document(row) if row is not None else None

    def get_demo_scenario(self, idempotency_key: str) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM demo_scenarios WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return self._demo_scenario_document(row) if row is not None else None

    def reset_demo_scenario(self, idempotency_key: str) -> dict:
        now = int(time.time())
        with self._lock:
            cursor = self._connection.execute(
                """UPDATE demo_scenarios SET reset_at = COALESCE(reset_at, ?)
                WHERE idempotency_key = ?""",
                (now, idempotency_key),
            )
            if cursor.rowcount != 1:
                raise KeyError(idempotency_key)
            self._connection.commit()
            row = self._connection.execute(
                "SELECT * FROM demo_scenarios WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        assert row is not None
        return self._demo_scenario_document(row)

    def list_demo_scenarios(self) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM demo_scenarios ORDER BY created_at DESC LIMIT 20"
            ).fetchall()
        return [self._demo_scenario_document(row) for row in rows]

    @staticmethod
    def _demo_scenario_document(row: sqlite3.Row) -> dict:
        return {
            "idempotency_key": row["idempotency_key"],
            "scenario_id": row["scenario_id"],
            "issue_number": int(row["issue_number"]),
            "issue_uri": row["issue_uri"],
            "program_id": f"program-{int(row['issue_number'])}",
            "created_at": int(row["created_at"]),
            "reset_at": int(row["reset_at"]) if row["reset_at"] else None,
        }

    def desire_activity(self, program_id: str, document: dict) -> dict:
        """Persist one canonical desired generic activity document.

        Revision advances only when source-owned content changes. Projection
        bookkeeping is excluded from the content, so retries and restarts replay
        the same revision and bytes.
        """
        without_revision = dict(document)
        without_revision.pop("revision", None)
        source_raw = json.dumps(
            without_revision, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        source_digest = "sha256:" + hashlib.sha256(source_raw.encode()).hexdigest()
        now = int(time.time())
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM activity_projections WHERE program_id = ?", (program_id,)
            ).fetchone()
            if row is not None:
                existing = json.loads(row["document_json"])
                existing.pop("revision", None)
                existing_raw = json.dumps(
                    existing, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                )
                existing_source_digest = (
                    "sha256:" + hashlib.sha256(existing_raw.encode()).hexdigest()
                )
                if existing_source_digest == source_digest:
                    return json.loads(row["document_json"])
                revision = int(row["revision"]) + 1
            else:
                revision = 1
            desired = {**without_revision, "revision": revision}
            raw = json.dumps(
                desired, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            )
            digest = "sha256:" + hashlib.sha256(raw.encode()).hexdigest()
            self._connection.execute(
                """INSERT INTO activity_projections
                (program_id, revision, content_digest, document_json,
                 published_digest, last_error, attempts, updated_at)
                VALUES (?, ?, ?, ?, NULL, NULL, 0, ?)
                ON CONFLICT(program_id) DO UPDATE SET
                    revision = excluded.revision,
                    content_digest = excluded.content_digest,
                    document_json = excluded.document_json,
                    last_error = NULL,
                    updated_at = excluded.updated_at""",
                (program_id, revision, digest, raw, now),
            )
            self._connection.commit()
        return desired

    def activity_target(self, program_id: str) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                """SELECT * FROM activity_projections
                WHERE program_id = ?
                AND (published_digest IS NULL OR published_digest != content_digest)""",
                (program_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "program_id": row["program_id"],
            "document": json.loads(row["document_json"]),
            "content_digest": row["content_digest"],
        }

    def activity_targets(self) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                """SELECT * FROM activity_projections
                WHERE published_digest IS NULL OR published_digest != content_digest
                ORDER BY updated_at, program_id LIMIT 100"""
            ).fetchall()
        return [
            {
                "program_id": row["program_id"],
                "document": json.loads(row["document_json"]),
                "content_digest": row["content_digest"],
            }
            for row in rows
        ]

    def activity_succeeded(self, program_id: str, digest: str) -> None:
        with self._lock:
            self._connection.execute(
                """UPDATE activity_projections
                SET published_digest = ?, last_error = NULL, attempts = attempts + 1,
                    updated_at = ?
                WHERE program_id = ? AND content_digest = ?""",
                (digest, int(time.time()), program_id, digest),
            )
            self._connection.commit()

    def activity_failed(self, program_id: str, digest: str, message: str) -> None:
        with self._lock:
            self._connection.execute(
                """UPDATE activity_projections
                SET last_error = ?, attempts = attempts + 1, updated_at = ?
                WHERE program_id = ? AND content_digest = ?""",
                (
                    " ".join(str(message).split())[:1000],
                    int(time.time()),
                    program_id,
                    digest,
                ),
            )
            self._connection.commit()

    def claim_deployment(self, request_id: str, program_id: str) -> bool:
        """Claim or recover one source action using its stable operation identity."""
        now = int(time.time())
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM deployment_requests WHERE request_id = ?", (request_id,)
            ).fetchone()
            if row is not None:
                if row["program_id"] != program_id:
                    raise ValueError("deployment request changed program identity")
                return row["state"] == "running"
            self._connection.execute(
                """INSERT INTO deployment_requests
                (request_id, program_id, state, attempts, created_at, updated_at)
                VALUES (?, ?, 'running', 1, ?, ?)""",
                (request_id, program_id, now, now),
            )
            self._connection.commit()
        return True

    def complete_deployment(self, request_id: str, result: dict) -> None:
        raw = json.dumps(result, sort_keys=True, separators=(",", ":"))
        now = int(time.time())
        with self._lock:
            row = self._connection.execute(
                "SELECT program_id FROM deployment_requests WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if row is None:
                raise KeyError(request_id)
            self._connection.execute(
                """UPDATE deployment_requests
                SET state = 'succeeded', result_json = ?, error = NULL, updated_at = ?
                WHERE request_id = ? AND state = 'running'""",
                (raw, now, request_id),
            )
            self._connection.commit()
        self.record_program_event(
            str(row["program_id"]),
            "product-deployed",
            "service.deployed",
            "Accepted product deployed",
            phase="succeeded",
            summary="The source-side runner independently verified the public endpoint.",
            links=[
                {
                    "rel": "endpoint",
                    "label": "Open application",
                    "url": str(result["endpoint"]),
                }
            ],
            attributes={
                "deployment_id": str(result["deployment_id"]),
                "session_name": str(result["session_name"]),
            },
            occurred_at=now,
        )

    def fail_deployment(self, request_id: str, message: str) -> None:
        with self._lock:
            self._connection.execute(
                """UPDATE deployment_requests
                SET state = 'failed', error = ?, updated_at = ?
                WHERE request_id = ? AND state = 'running'""",
                (" ".join(str(message).split())[:1000], int(time.time()), request_id),
            )
            self._connection.commit()

    def deployment_count(self, program_id: str) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS total FROM deployment_requests WHERE program_id = ?",
                (program_id,),
            ).fetchone()
        return int(row["total"]) if row is not None else 0

    def latest_deployment(self, program_id: str) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                """SELECT * FROM deployment_requests
                WHERE program_id = ?
                ORDER BY updated_at DESC LIMIT 1""",
                (program_id,),
            ).fetchone()
        return self._deployment_document(row) if row is not None else None

    def get_deployment(self, request_id: str) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM deployment_requests WHERE request_id = ?", (request_id,)
            ).fetchone()
        return self._deployment_document(row) if row is not None else None

    @staticmethod
    def _deployment_document(row: sqlite3.Row) -> dict:
        return {
            "request_id": row["request_id"],
            "program_id": row["program_id"],
            "state": row["state"],
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "error": row["error"],
            "attempts": int(row["attempts"]),
            "created_at": int(row["created_at"]),
            "updated_at": int(row["updated_at"]),
        }

    def get_program(self, program_id: str) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM programs WHERE program_id = ?", (program_id,)
            ).fetchone()
            features = self._connection.execute(
                "SELECT * FROM program_features WHERE program_id = ? ORDER BY ordinal",
                (program_id,),
            ).fetchall()
        if row is None:
            return None
        return self._program_document(row, features)

    def get_program_by_issue(self, repository: str, issue_number: int) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM programs WHERE repository = ? AND issue_number = ?",
                (repository, issue_number),
            ).fetchone()
            features = (
                self._connection.execute(
                    "SELECT * FROM program_features WHERE program_id = ? ORDER BY ordinal",
                    (row["program_id"],),
                ).fetchall()
                if row is not None
                else []
            )
        return self._program_document(row, features) if row is not None else None

    def _program_issue(self, program_id: str) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT issue_number FROM programs WHERE program_id = ?", (program_id,)
            ).fetchone()
        if row is None:
            raise KeyError(program_id)
        return int(row["issue_number"])

    def _program_repository(self, program_id: str) -> str:
        with self._lock:
            row = self._connection.execute(
                "SELECT repository FROM programs WHERE program_id = ?", (program_id,)
            ).fetchone()
        if row is None:
            raise KeyError(program_id)
        return str(row["repository"])

    def _update_program(self, program_id: str, **values: Any) -> None:
        values["updated_at"] = int(time.time())
        assignments = ", ".join(f"{column} = ?" for column in values)
        with self._lock:
            cursor = self._connection.execute(
                f"UPDATE programs SET {assignments} WHERE program_id = ?",
                (*values.values(), program_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(program_id)
            self._connection.commit()

    def _update_feature(self, program_id: str, feature_id: str, **values: Any) -> None:
        values["updated_at"] = int(time.time())
        assignments = ", ".join(f"{column} = ?" for column in values)
        with self._lock:
            cursor = self._connection.execute(
                f"UPDATE program_features SET {assignments} WHERE program_id = ? AND feature_id = ?",
                (*values.values(), program_id, feature_id),
            )
            if cursor.rowcount != 1:
                raise KeyError((program_id, feature_id))
            self._connection.commit()

    @staticmethod
    def _feature_document(row: sqlite3.Row) -> dict:
        return {
            "id": row["feature_id"],
            "ordinal": int(row["ordinal"]),
            "title": row["title"],
            "summary": row["summary"],
            "acceptance_criteria": json.loads(row["criteria_json"]),
            "dependencies": json.loads(row["dependencies_json"]),
            "status": row["status"],
            "issue_number": row["issue_number"],
            "issue_uri": row["issue_uri"],
            "delivery_id": row["delivery_id"],
            "pr_number": row["pr_number"],
            "pr_uri": row["pr_uri"],
            "head_commit": row["head_commit"],
            "merged_commit": row["merged_commit"],
            "error": row["error"],
        }

    @classmethod
    def _program_document(cls, row: sqlite3.Row, features: list[sqlite3.Row]) -> dict:
        return {
            "program_id": row["program_id"],
            "repository": row["repository"],
            "issue_number": int(row["issue_number"]),
            "issue_uri": row["issue_uri"],
            "status": row["status"],
            "brd": {
                "delivery_id": row["brd_delivery_id"],
                "pr_number": row["brd_pr_number"],
                "pr_uri": row["brd_pr_uri"],
                "head_commit": row["brd_head_commit"],
                "path": row["brd_path"],
                "digest": row["brd_digest"],
                "approved_commit": row["approved_commit"],
                "approved_by": row["approved_by"],
                "approved_at": row["approved_at"],
            },
            "plan_digest": row["plan_digest"],
            "features": [cls._feature_document(feature) for feature in features],
            "acceptance": json.loads(row["acceptance_json"])
            if row["acceptance_json"]
            else None,
            "error": row["error"],
            "created_at": int(row["created_at"]),
            "updated_at": int(row["updated_at"]),
        }

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
            workflow_kind=row["workflow_kind"],
            program_id=row["program_id"],
            feature_id=row["feature_id"],
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
                "details": (
                    json.loads(projection["details_json"])
                    if projection["details_json"]
                    else None
                ),
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
            "workflow_kind": row["workflow_kind"],
            "program_id": row["program_id"],
            "feature_id": row["feature_id"],
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
