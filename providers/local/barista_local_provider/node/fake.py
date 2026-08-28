"""In-memory fake Node backend.

A legitimate node backend for single-machine development and offline tests —
the analogue of the kernel's `fake` runtime (tooling, no hypervisor). It holds
disk-level lifecycle honestly (create/start/pause/resume/destroy/exec) and does
NOT claim memory snapshot, fork, or CoW, so the provider advertises only what
is real. Its state is persisted to the data dir so it survives a restart.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Optional

from .client import (
    ExecResult,
    InstanceRequest,
    NodeCapabilities,
    NodeInstance,
    NodeNotFound,
)


class FakeNodeClient:
    def __init__(self, state_path: Optional[Path] = None):
        self._state_path = Path(state_path) if state_path else None
        self._instances: dict[str, dict] = {}
        self._load()

    # -- persistence ------------------------------------------------------ #
    def _load(self) -> None:
        if self._state_path and self._state_path.exists():
            self._instances = json.loads(self._state_path.read_text())

    def _save(self) -> None:
        if self._state_path:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(json.dumps(self._instances))

    # -- NodeClient ------------------------------------------------------- #
    def node_info(self) -> NodeCapabilities:
        # Honest: disk lifecycle and pause/resume only. No exact memory, no fork.
        return NodeCapabilities(
            pause_resume=True,
            memory_snapshot=False,
            disk_snapshot=True,
            cow_fork=False,
            guest_agent=True,
        )

    def create_and_start(self, request: InstanceRequest) -> NodeInstance:
        self._instances[request.instance_id] = {
            "state": "running",
            "ready": True,
            "image": request.image,
            "digest": request.digest,
            "start_cmd": request.start_cmd,
            "env": request.env,
            "workdir": request.workdir,
        }
        self._save()
        return NodeInstance(instance_id=request.instance_id, state="running", ready=True)

    def get(self, instance_id: str) -> Optional[NodeInstance]:
        rec = self._instances.get(instance_id)
        if not rec:
            return None
        return NodeInstance(instance_id=instance_id, state=rec["state"], ready=rec.get("ready", True))

    def destroy(self, instance_id: str) -> None:
        self._instances.pop(instance_id, None)
        self._save()

    def pause(self, instance_id: str) -> None:
        rec = self._require(instance_id)
        rec["state"] = "paused"
        self._save()

    def resume(self, instance_id: str) -> None:
        rec = self._require(instance_id)
        rec["state"] = "running"
        self._save()

    def exec(
        self,
        instance_id: str,
        command: list[str],
        env: Optional[dict[str, str]] = None,
        workdir: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ) -> ExecResult:
        instance = self._require(instance_id)
        # `printenv NAME` is the smallest black-box observation that exec runs in
        # the session environment rather than an unrelated agent environment.
        # Per-exec values override the base process exactly as a real node does.
        effective_env = {**instance.get("env", {}), **(env or {})}
        if len(command) == 2 and command[0] == "printenv":
            value = effective_env.get(command[1])
            if value is None:
                return ExecResult(exit_code=1, stdout=b"", stderr=b"")
            return ExecResult(exit_code=0, stdout=(value + "\n").encode(), stderr=b"")

        # A deterministic echo for commands the tooling backend does not execute.
        rendered = " ".join(shlex.quote(c) for c in command)
        return ExecResult(exit_code=0, stdout=(rendered + "\n").encode(), stderr=b"")

    def close(self) -> None:
        self._save()

    # -- helpers ---------------------------------------------------------- #
    def _require(self, instance_id: str) -> dict:
        rec = self._instances.get(instance_id)
        if not rec:
            raise NodeNotFound(instance_id)
        return rec
