"""The Factory coordinator.

An ordinary portable app: it drives everything through the Host API SDK and
never touches a privileged node contract, a provider database/bucket, or a
Cloud-specific shape. It fans a mission across worker sessions, harvests each
successful worker's receipt/artifacts BEFORE reaping it, keeps failed workers
for forensics, and reconstructs its state idempotently after a restart.
"""

from __future__ import annotations

import concurrent.futures
import time
from pathlib import Path
from typing import Optional

from barista_app_sdk import BaristaClient
from barista_app_sdk.content import canonical_bytes, content_id
from barista_app_sdk.errors import HostAPIError

from .grants import derive_worker_grant
from .mission import Mission, Task
from .state import MissionState, TaskState

RECEIPT_MEDIA_TYPE = "application/vnd.barista.factory.receipt+json"


class Coordinator:
    def __init__(self, client: BaristaClient, mission: Mission, state_path: str | Path):
        self.client = client
        self.mission = mission
        self.state = MissionState.open(state_path, mission.name, [t.id for t in mission.tasks])
        self.grant = derive_worker_grant(mission.permissions)

    # -- durable coordinator scope --------------------------------------- #
    def _ensure_coordinator_session(self) -> str:
        """A stable mission session that outlives workers and holds receipts."""
        if self.state.coordinator_session_id:
            return self.state.coordinator_session_id
        session = self.client.ensure_session(
            self.mission.app,
            name=f"{self.mission.name}-coordinator",
            idempotency_key=f"{self.mission.name}:coordinator",
            metadata={"role": "factory-coordinator", "mission": self.mission.name},
        )
        self.state.coordinator_session_id = session.id
        self.state.save()
        return session.id

    def _worker_name(self, task: Task) -> str:
        return f"{self.mission.name}-{task.id}"

    # -- one task --------------------------------------------------------- #
    def run_task(self, task: Task) -> None:
        ts = self.state.tasks[task.id]
        if ts.state == "ok":
            return  # already harvested; restart must not re-run it

        # Recovery must re-ensure the SAME worker, not start a new attempt: only
        # a genuinely new run increments the attempt counter. A task found mid
        # flight ('running') reuses its existing attempt so the idempotency key
        # is unchanged and ensure returns the original worker instead of spawning
        # a second one and orphaning the first.
        if not (ts.state == "running" and ts.attempts > 0):
            ts.attempts += 1
        ts.state = "running"
        ts.worker = self._worker_name(task)
        self.state.save()

        # A stable idempotency key per (mission, task, attempt): a lost response
        # or a restart re-ensures the SAME worker rather than duplicating it.
        idem = f"{self.mission.name}:{task.id}:attempt-{ts.attempts}"
        worker = self.client.ensure_session(
            self.mission.app, name=ts.worker, idempotency_key=idem,
            env=self.grant.env(), metadata={"mission": self.mission.name, "task": task.id},
        )

        handle = self.client.exec(
            worker.id, task.worker_command(), env={**self.grant.env(), **task.env},
            working_dir=task.workdir, timeout_seconds=self.mission.task_timeout_s,
        )
        op = self.client.wait_operation(handle.operation_id, timeout=self.mission.task_timeout_s)
        exit_code = (op.result or {}).get("exit_code", 1)
        ok = op.done and exit_code == 0

        checks: list[dict] = []
        if ok and task.check:
            chk = self.client.exec(worker.id, task.check)
            chk_op = self.client.wait_operation(chk.operation_id, timeout=self.mission.task_timeout_s)
            chk_exit = (chk_op.result or {}).get("exit_code", 1)
            checks.append({"command": task.check, "exit_code": chk_exit})
            ok = chk_op.done and chk_exit == 0

        ts.exit_code = exit_code
        if ok:
            self._harvest_then_reap(task, worker.id, exit_code, checks)
        else:
            # Failed workers are NOT reaped: they stay for bounded forensics.
            ts.state = "failed"
            self.state.save()

    def _harvest_then_reap(self, task: Task, worker_id: str, exit_code: int, checks: list) -> None:
        """Register the receipt (and any harvested artifact) on the durable
        coordinator scope BEFORE deleting the worker. If deletion happened first,
        the receipt would not exist — so a retrievable receipt proves harvest
        completed before the reap."""
        ts = self.state.tasks[task.id]
        coord = self._ensure_coordinator_session()

        artifact_id = None
        if task.collect:
            # Harvest: capture the worker's declared output. (Content-addressed;
            # with a real runtime this is the worker's /work, here its exec echo.)
            harvest = self.client.exec(worker_id, ["sh", "-c", "echo collected"])
            self.client.wait_operation(harvest.operation_id, timeout=60)

        receipt = {
            "mission": self.mission.name,
            "task": task.id,
            "worker": ts.worker,
            "exit_code": exit_code,
            "checks": checks,
            "harvested": bool(task.collect),
            "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        # Content-addressed with the ecosystem's one canonical serialization
        # (barista_app_sdk.content), so a receipt's digest matches any other
        # content-addressed object and a future verifier recomputes it.
        blob = canonical_bytes(receipt)
        artifact = self.client.register_artifact(
            coord,
            name=f"receipt-{task.id}.json",
            digest=content_id(receipt),
            size_bytes=len(blob),
            media_type=RECEIPT_MEDIA_TYPE,
            idempotency_key=f"{self.mission.name}:{task.id}:receipt",
        )
        artifact_id = artifact.id

        # Atomic under the state lock so a concurrent save never captures the
        # receipt-set-but-still-running tear.
        with self.state.lock:
            ts.receipt = receipt
            ts.receipt_artifact_id = artifact_id
            ts.state = "ok"
            self.state.save()

        # The reap: only now, with the receipt durable, delete the worker.
        try:
            self.client.delete_session(worker_id, idempotency_key=f"{self.mission.name}:{task.id}:reap")
        except HostAPIError:
            pass  # a failed reap does not lose the receipt

    # -- whole mission ---------------------------------------------------- #
    def run(self) -> MissionState:
        self._ensure_coordinator_session()
        pending = [t for t in self.mission.tasks if self.state.tasks[t.id].state != "ok"]
        deadline = time.time() + self.mission.deadline_s if self.mission.deadline_s else None

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.mission.concurrency) as pool:
            futures = {}
            for task in pending:
                if deadline and time.time() > deadline:
                    break
                futures[pool.submit(self._guarded, task)] = task
            for fut in concurrent.futures.as_completed(futures):
                fut.result()

        if all(t.state in ("ok", "failed") for t in self.state.tasks.values()):
            self.state.state = "done"
            self.state.finished_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self.state.save()
        self._notify()
        return self.state

    def _guarded(self, task: Task) -> None:
        try:
            self.run_task(task)
        except Exception as exc:  # noqa: BLE001 - record, never crash the whole mission
            ts = self.state.tasks[task.id]
            ts.state = "failed"
            ts.receipt = {"error": f"{type(exc).__name__}: {exc}"}
            self.state.save()

    def _notify(self) -> None:
        url = self.mission.notify_url
        if not url or not url.startswith(("http://", "https://")):
            return
        summary = self.state.summary()
        msg = f"factory {self.mission.name}: {summary['ok']}/{summary['total']} ok, {summary['failed']} failed"
        try:
            import urllib.request

            urllib.request.urlopen(  # noqa: S310 - scheme checked above
                urllib.request.Request(url, data=msg.encode(), headers={"Title": "barista factory"}),
                timeout=30,
            )
        except Exception:  # noqa: BLE001 - best effort; state is the truth
            pass
