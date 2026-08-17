"""Durable mission state.

The coordinator's truth: task status, worker handles, attempts, and receipts,
persisted to a JSON file after every transition so a restart reconstructs the
mission and never duplicates an accepted worker. In a full deployment this lives
under a coordinator session or provider artifact scope; the on-disk form is the
documented, recoverable representation.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class TaskState:
    id: str
    state: str = "pending"  # pending|running|ok|failed
    worker: Optional[str] = None
    attempts: int = 0
    exit_code: Optional[int] = None
    receipt_artifact_id: Optional[str] = None
    receipt: Optional[dict] = None


@dataclass
class MissionState:
    mission: str
    coordinator_session_id: Optional[str] = None
    state: str = "running"  # running|done
    started_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    finished_at: Optional[str] = None
    tasks: dict[str, TaskState] = field(default_factory=dict)
    _path: Optional[Path] = None

    # -- persistence ------------------------------------------------------ #
    @classmethod
    def open(cls, path: str | Path, mission_name: str, task_ids: list[str]) -> "MissionState":
        p = Path(path)
        if p.exists():
            state = cls.from_dict(json.loads(p.read_text()))
        else:
            state = cls(mission=mission_name, tasks={tid: TaskState(id=tid) for tid in task_ids})
        state._path = p
        # Ensure any newly-added task ids exist.
        for tid in task_ids:
            state.tasks.setdefault(tid, TaskState(id=tid))
        return state

    def save(self) -> None:
        if not self._path:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = self.to_dict()
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent), suffix=".tmp")
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=1)
        os.replace(tmp, self._path)

    def to_dict(self) -> dict:
        return {
            "mission": self.mission,
            "coordinator_session_id": self.coordinator_session_id,
            "state": self.state,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "tasks": {tid: asdict(ts) for tid, ts in self.tasks.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MissionState":
        tasks = {tid: TaskState(**ts) for tid, ts in d.get("tasks", {}).items()}
        return cls(
            mission=d["mission"],
            coordinator_session_id=d.get("coordinator_session_id"),
            state=d.get("state", "running"),
            started_at=d.get("started_at"),
            finished_at=d.get("finished_at"),
            tasks=tasks,
        )

    # -- summary ---------------------------------------------------------- #
    def summary(self) -> dict:
        states = [t.state for t in self.tasks.values()]
        return {
            "total": len(states),
            "ok": states.count("ok"),
            "failed": states.count("failed"),
            "pending": states.count("pending") + states.count("running"),
        }
