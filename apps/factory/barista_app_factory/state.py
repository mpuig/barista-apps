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
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class TaskState:
    id: str
    state: str = "pending"  # pending|running|ok|failed|blocked
    worker: Optional[str] = None
    attempts: int = 0
    exit_code: Optional[int] = None
    receipt_artifact_id: Optional[str] = None
    receipt: Optional[dict] = None
    blocked_by: Optional[str] = None
    """The dependency that did not succeed, when this task is `blocked`.

    Blocked is not failed. A task whose dependency failed has learned nothing
    about itself and never ran, so reporting it as a failure sends someone to
    debug work that never happened — the same reasoning `_blame_the_operator`
    already applies when it returns an unattempted task to `pending`. It is not
    `pending` either: pending will be attempted, blocked will not.
    """
    outputs: dict[str, str] = field(default_factory=dict)
    """Digests of what this task produced, by output name.

    Digests, not content: the bytes live in the coordinator session, and a copy
    here would be a second record of the same thing that can disagree with it.
    Recorded so a consumer's transfer can be verified against what the producer
    actually emitted.
    """


@dataclass
class MissionState:
    mission: str
    coordinator_session_id: Optional[str] = None
    state: str = "running"  # running|done|lost_authority
    started_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    finished_at: Optional[str] = None
    authority_lost: Optional[str] = None
    """Why the coordinator can no longer act, if that is what ended the mission.

    Separate from any task's state on purpose. A lapsed or refused credential
    says nothing about the work: it is an operator problem (provision a new
    grant), and a mission that recorded it as a failed task would send someone
    to debug a task that never ran.
    """
    credential: dict = field(default_factory=dict)
    """How the coordinator's own credential was kept alive: whether refresh was
    active, how many times it rotated, the observed lifetime, and the margin it
    refreshed on. Recorded so the choice is auditable after the fact."""
    tasks: dict[str, TaskState] = field(default_factory=dict)
    _path: Optional[Path] = None
    # Guards mutate+save sequences: coordinator worker threads share this state,
    # so a save must snapshot a consistent view (never a receipt-saved-but-still-
    # running tear) and two writers must not interleave.
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False, compare=False)

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
        with self.lock:
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
            "authority_lost": self.authority_lost,
            "credential": self.credential,
            "tasks": {tid: asdict(ts) for tid, ts in self.tasks.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MissionState":
        tasks = {tid: TaskState(**ts) for tid, ts in d.get("tasks", {}).items()}
        return cls(
            mission=d["mission"],
            coordinator_session_id=d.get("coordinator_session_id"),
            # A restart is a fresh attempt at the work: the previous run's lost
            # authority is history, not the new run's outcome.
            state="running" if d.get("state") == "lost_authority" else d.get("state", "running"),
            started_at=d.get("started_at"),
            finished_at=d.get("finished_at"),
            authority_lost=None,
            credential=d.get("credential", {}),
            tasks=tasks,
        )

    # -- summary ---------------------------------------------------------- #
    def summary(self) -> dict:
        states = [t.state for t in self.tasks.values()]
        out = {
            "total": len(states),
            "ok": states.count("ok"),
            "failed": states.count("failed"),
            "pending": states.count("pending") + states.count("running"),
        }
        # Only when there are any, so the summary of a mission without
        # dependencies is byte-identical to what it was before they existed.
        blocked = states.count("blocked")
        if blocked:
            out["blocked"] = blocked
        return out
