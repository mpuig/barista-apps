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
from barista_app_sdk.errors import AuthenticationError, HostAPIError

from .credential import CredentialKeeper, LostAuthority
from .grants import derive_worker_grant
from . import transfer
from .mission import Mission, Task
from .state import MissionState, TaskState

RECEIPT_MEDIA_TYPE = "application/vnd.barista.factory.receipt+json"
MISSION_RESULT_MEDIA_TYPE = "application/vnd.barista.factory.mission-result+json"


class Coordinator:
    def __init__(
        self,
        client: BaristaClient,
        mission: Mission,
        state_path: str | Path,
        *,
        credential: Optional[CredentialKeeper] = None,
    ):
        self.client = client
        self.mission = mission
        self.state = MissionState.open(state_path, mission.name, [t.id for t in mission.tasks])
        self.grant = derive_worker_grant(mission.permissions)
        # The coordinator's own credential outlives one grant lifetime only if
        # something refreshes it. Injectable so a test can drive the clock.
        self.credential = credential if credential is not None else CredentialKeeper(client)
        self.credential.set_status_callback(self._persist_credential_status)

    def _persist_credential_status(self, status: dict) -> None:
        """Keep renewal evidence observable while the workload still lives."""
        with self.state.lock:
            self.state.credential = status
            self.state.save()

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

    # -- authority -------------------------------------------------------- #
    def _checkpoint_authority(self) -> None:
        """Refresh the credential if it is due, and surface a loss as a loss.

        Called at the points where the coordinator is about to need authority.
        The keeper's ticker covers the gaps *inside* a long call — a task with an
        hour's timeout outlives a fifteen-minute grant without ever reaching one
        of these checkpoints — and this is where its verdict is read.
        """
        if self.credential.lost_authority:
            raise LostAuthority(self.credential.lost_authority)
        self.credential.ensure_fresh()

    def _record_lost_authority(self, reason: str) -> None:
        with self.state.lock:
            self.state.state = "lost_authority"
            self.state.authority_lost = reason
            self.state.finished_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self.state.credential = self.credential.status()
            self.state.save()

    # -- one task --------------------------------------------------------- #
    def run_task(self, task: Task) -> None:
        ts = self.state.tasks[task.id]
        if ts.state == "ok":
            return  # already harvested; restart must not re-run it
        self._checkpoint_authority()

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

        # Everything the task is given, before it runs: content the mission
        # planted, and content a dependency produced. Both land before the
        # command so the task sees a world that is already set up.
        self._place_inputs(task, worker.id)

        handle = self.client.exec(
            worker.id, task.worker_command(), env={**self.grant.env(), **task.env},
            working_dir=task.workdir, timeout_seconds=self.mission.task_timeout_s,
        )
        op = self.client.wait_operation(handle.operation_id, timeout=self.mission.task_timeout_s)
        exit_code = (op.result or {}).get("exit_code", 1)
        ok = op.done and exit_code == 0

        checks: list[dict] = []
        if ok and task.check:
            # Re-assert the planted criterion before it judges. This is the half
            # of the gate guarantee that needs no opt-in (design D3): a worker
            # that overwrote the planted content — deliberately or by generating
            # a file of the same name — is judged against the mission's version,
            # not its own. Idempotent, so it costs nothing when untouched.
            self._plant(task, worker.id)
            chk = self.client.exec(worker.id, task.check)
            chk_op = self.client.wait_operation(chk.operation_id, timeout=self.mission.task_timeout_s)
            chk_exit = (chk_op.result or {}).get("exit_code", 1)
            checks.append({"command": task.check, "exit_code": chk_exit})
            ok = chk_op.done and chk_exit == 0

        if ok and task.produces:
            # Before the harvest, and therefore before the reap: a produced
            # output that is not captured while its worker lives is one no
            # dependent can ever receive.
            self._capture_outputs(task, worker.id)

        ts.exit_code = exit_code
        if ok:
            # The most expensive moment to lose authority: the work is done and
            # the receipt is not yet durable. Refresh first if the margin is up.
            self._checkpoint_authority()
            self._harvest_then_reap(task, worker.id, exit_code, checks)
        elif ts.attempts < self.mission.max_attempts:
            # A retry reuses the same durable worker but receives a new attempt
            # idempotency key. Keeping the worker preserves the failed attempt's
            # workspace and avoids pretending a retry erased its evidence.
            ts.state = "pending"
            self.state.save()
        else:
            # Failed workers are NOT reaped: they stay for bounded forensics.
            # Their final receipt is still durable on the coordinator scope.
            self._record_failure(task, worker.id, exit_code, checks)

    # -- inputs, outputs, and the staging scope ---------------------------- #
    def _staged(self, task_id: str, name: str) -> str:
        """Where a produced output rests between its producer and its consumers.

        Inside the coordinator's own session, which is the one scope that
        outlives every worker — the same durability argument that puts receipts
        there. Namespaced by mission and task so two tasks producing `spec` do
        not collide.
        """
        return f"/factory/{self.mission.name}/{task_id}/{name}"

    def _plant(self, task: Task, worker_id: str) -> None:
        """Place the mission's own content into the worker's session."""
        for path, content in task.files.items():
            transfer.write_file(self.client, worker_id, path, content)

    def _place_inputs(self, task: Task, worker_id: str) -> None:
        self._plant(task, worker_id)
        if not task.consumes:
            return
        coord = self._ensure_coordinator_session()
        producers = {
            name: dep
            for dep in task.depends_on
            for name in self.mission.by_id()[dep].produces
        }
        for name, dest in task.consumes.items():
            # The mission is validated at load, so a consumed output always has a
            # producer among this task's dependencies, and that producer is `ok`
            # or this task would not be ready.
            src = self._staged(producers[name], name)
            transfer.write_file(self.client, worker_id, dest, transfer.read_file(self.client, coord, src))

    def _capture_outputs(self, task: Task, worker_id: str) -> None:
        """Move each declared output into the coordinator scope, and record its digest.

        Worker → coordinator → worker, never worker → worker (design D2): a
        direct copy would need both workers alive at once, which is exactly what
        reap-on-success gives up.
        """
        ts = self.state.tasks[task.id]
        coord = self._ensure_coordinator_session()
        for name, path in task.produces.items():
            raw = transfer.read_file(self.client, worker_id, path)
            digest = transfer.write_file(self.client, coord, self._staged(task.id, name), raw)
            with self.state.lock:
                ts.outputs[name] = digest
                self.state.save()
            # A listable, durable reference to what this task emitted. The
            # registry records references rather than bytes, which is why the
            # bytes rest in the coordinator session and only the digest is
            # registered here.
            self.client.register_artifact(
                coord,
                name=f"{task.id}-{name}",
                digest=digest,
                size_bytes=len(raw),
                media_type="application/octet-stream",
                idempotency_key=f"{self.mission.name}:{task.id}:out:{name}",
            )

    def _register_receipt(
        self, task: Task, worker_id: str, exit_code: int, checks: list, *, outcome: str
    ) -> None:
        """Make the final attempt observable before its worker can disappear."""
        ts = self.state.tasks[task.id]
        coord = self._ensure_coordinator_session()
        receipt = {
            "mission": self.mission.name,
            "task": task.id,
            "worker": ts.worker,
            "outcome": outcome,
            "attempts": ts.attempts,
            "exit_code": exit_code,
            "checks": checks,
            "harvested": bool(task.collect) if outcome == "ok" else False,
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

        # Atomic under the state lock so a concurrent save never captures the
        # receipt-set-but-still-running tear.
        with self.state.lock:
            ts.receipt = receipt
            ts.receipt_artifact_id = artifact.id
            ts.state = outcome
            self.state.save()

    def _record_failure(self, task: Task, worker_id: str, exit_code: int, checks: list) -> None:
        self._register_receipt(task, worker_id, exit_code, checks, outcome="failed")

    def _harvest_then_reap(self, task: Task, worker_id: str, exit_code: int, checks: list) -> None:
        """Register the receipt (and any harvested artifact) on the durable
        coordinator scope BEFORE deleting the worker. If deletion happened first,
        the receipt would not exist — so a retrievable receipt proves harvest
        completed before the reap."""
        if task.collect:
            # Harvest: capture the worker's declared output. (Content-addressed;
            # with a real runtime this is the worker's /work, here its exec echo.)
            harvest = self.client.exec(worker_id, ["sh", "-c", "echo collected"])
            self.client.wait_operation(harvest.operation_id, timeout=60)

        self._register_receipt(task, worker_id, exit_code, checks, outcome="ok")

        # The reap: only now, with the receipt durable, delete the worker.
        try:
            self.client.delete_session(worker_id, idempotency_key=f"{self.mission.name}:{task.id}:reap")
        except HostAPIError:
            pass  # a failed reap does not lose the receipt

    # -- whole mission ---------------------------------------------------- #
    def run(self) -> MissionState:
        with self.credential.running():
            self._run_inside_credential()
        self._notify()
        return self.state

    def _run_inside_credential(self) -> None:
        try:
            self._ensure_coordinator_session()
        except LostAuthority as exc:
            self._record_lost_authority(str(exc))
            return
        except AuthenticationError as exc:
            self._record_lost_authority(self._authentication_reason(exc))
            return

        deadline = time.time() + self.mission.deadline_s if self.mission.deadline_s else None
        self._schedule(deadline)

        self.state.credential = self.credential.status()
        if self.state.authority_lost:
            # Not 'done', and emphatically not 'failed': the work is unfinished,
            # and unfinished is not the same as attempted and refused.
            self.state.save()
            return
        if all(t.state in ("ok", "failed", "blocked") for t in self.state.tasks.values()):
            # 'done' means the mission ran to a conclusion, not that everything
            # passed — as it did before blocked existed, when a failed task still
            # left the mission done. `summary()` carries the counts.
            self.state.state = "done"
            self.state.finished_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.state.save()
        if self.state.state == "done":
            self._register_mission_result()

    def _register_mission_result(self) -> None:
        """Leave durable evidence that the coordinator reached its conclusion."""
        result = {
            "mission": self.mission.name,
            "state": self.state.state,
            "summary": self.state.summary(),
            "credential": self.state.credential,
            "authority_lost": self.state.authority_lost,
        }
        blob = canonical_bytes(result)
        self.client.register_artifact(
            self._ensure_coordinator_session(),
            name="mission-result.json",
            digest=content_id(result),
            size_bytes=len(blob),
            media_type=MISSION_RESULT_MEDIA_TYPE,
            idempotency_key=f"{self.mission.name}:result",
        )

    # -- scheduling -------------------------------------------------------- #
    def _ready(self) -> list[Task]:
        """Tasks that may start now: pending, with every dependency succeeded.

        Derived on every pass and never persisted (design D5). A stored ready set
        is a second record of a fact the task states already hold, and after a
        crash the two can disagree — the failure this codebase has paid for
        before. Recomputing is cheap and cannot drift.
        """
        states = self.state.tasks
        return [
            t for t in self.mission.tasks
            # `running` counts as ready on purpose. Nothing is running in *this*
            # process when scheduling begins, so a task persisted that way is a
            # previous run that died mid-flight, and it must be picked up again —
            # `run_task` re-ensures it under its existing attempt rather than
            # starting a second one. Excluding it here would strand it forever.
            if states[t.id].state in ("pending", "running")
            and all(states[d].state == "ok" for d in t.depends_on)
        ]

    def _mark_unreachable(self) -> bool:
        """Block every task that can no longer run, and say whether any were.

        A task is unreachable when a dependency has failed or is itself blocked.
        Applied transitively: blocking only direct dependents would leave a
        task three hops downstream `pending` forever, and a mission that never
        finishes is a worse report than one that says why it stopped.
        """
        states = self.state.tasks
        changed = False
        for task in self.mission.tasks:
            if states[task.id].state != "pending":
                continue
            for dep in task.depends_on:
                if states[dep].state in ("failed", "blocked"):
                    states[task.id].state = "blocked"
                    states[task.id].blocked_by = dep
                    changed = True
                    break
        return changed

    def _schedule(self, deadline: float | None) -> None:
        """Run the graph, keeping `concurrency` slots busy.

        A ready set rather than levels: running the graph level by level would
        put a barrier between them, so one slow task would idle every worker
        until it finished. Here a slot is refilled the moment any task completes,
        so wall-clock follows the critical path instead of the sum of each
        level's slowest task.
        """
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.mission.concurrency) as pool:
            running: dict[concurrent.futures.Future, Task] = {}
            while True:
                self._mark_unreachable()
                if deadline and time.time() > deadline:
                    break
                if self.state.authority_lost:
                    # Submitting more would only produce more refusals, and every
                    # one of them would look like a task that failed.
                    break
                while len(running) < self.mission.concurrency:
                    ready = [t for t in self._ready() if t.id not in {x.id for x in running.values()}]
                    if not ready:
                        break
                    task = ready[0]
                    # The claim is this dict, not a state write. Only this thread
                    # submits, so membership here is enough to stop a second
                    # submission — and writing `running` to the state would
                    # collide with `run_task`'s recovery rule, which reads that
                    # same value to decide whether it is resuming an accepted
                    # attempt or starting a new one.
                    running[pool.submit(self._guarded, task)] = task
                if not running:
                    break
                # Wait for the first completion rather than all of them: that is
                # what refills a slot as soon as one frees, and what lets a
                # dependent start the instant its dependency lands.
                done, _ = concurrent.futures.wait(
                    running, return_when=concurrent.futures.FIRST_COMPLETED
                )
                for fut in done:
                    running.pop(fut)
                    fut.result()
            for fut in concurrent.futures.as_completed(list(running)):
                fut.result()
        # A dependency may have failed on the final pass, leaving dependents
        # pending with nothing left to run them.
        self._mark_unreachable()

    @staticmethod
    def _authentication_reason(exc: AuthenticationError) -> str:
        return (
            "the provider no longer accepts the coordinator's credential "
            f"({exc.code or exc.status}): {exc}. A delegated grant that has lapsed cannot "
            "be refreshed, so a new one must be provisioned"
        )

    def _guarded(self, task: Task) -> None:
        ts = self.state.tasks[task.id]
        try:
            self.run_task(task)
        except LostAuthority as exc:
            self._blame_the_operator(ts, str(exc))
        except AuthenticationError as exc:
            # The credential itself is not accepted. Distinct from an
            # AuthorizationError, which means the credential is live and the
            # action was refused — that is a permissions bug in the mission, and
            # it stays a task failure below.
            self._blame_the_operator(ts, self._authentication_reason(exc))
        except Exception as exc:  # noqa: BLE001 - record, never crash the whole mission
            ts.state = "failed"
            ts.receipt = {"error": f"{type(exc).__name__}: {exc}"}
            self.state.save()

    def _blame_the_operator(self, ts: TaskState, reason: str) -> None:
        """Record lost authority without blaming the task.

        A task the coordinator could not act on has learned nothing about
        itself. Leaving it 'running' would suggest a worker still going, and
        marking it 'failed' would send someone to debug work that never ran — so
        it goes back to 'pending', which is what it is.
        """
        if ts.state == "running":
            ts.state = "pending"
        self._record_lost_authority(reason)

    def _notify(self) -> None:
        url = self.mission.notify_url
        if not url or not url.startswith(("http://", "https://")):
            return
        summary = self.state.summary()
        msg = f"factory {self.mission.name}: {summary['ok']}/{summary['total']} ok, {summary['failed']} failed"
        if self.state.authority_lost:
            # Whoever reads this must be sent to the credential, not to the work.
            msg += f", LOST AUTHORITY ({summary['pending']} not attempted): {self.state.authority_lost}"
        try:
            import urllib.request

            urllib.request.urlopen(  # noqa: S310 - scheme checked above
                urllib.request.Request(url, data=msg.encode(), headers={"Title": "barista factory"}),
                timeout=30,
            )
        except Exception:  # noqa: BLE001 - best effort; state is the truth
            pass
