# Tasks

Ordered so each step is testable before the next depends on it. §1–§3 are what
unblock an in-session run; §4 onward is what that run then proves.

## 1. Mission delivery

- [x] 1.1 Make `$BARISTA_FACTORY_MISSION` the documented delivery mechanism in
      `apps/factory/README.md`, alongside the existing path argument.
- [x] 1.2 Test: a mission in the environment runs with no file present.
- [x] 1.3 Test: an explicit path that does not exist fails **naming the path**,
      while `$BARISTA_FACTORY_MISSION` holds a different valid mission. This
      pins `_load_mission`'s existing precedence against a future "helpful"
      fallback.
- [x] 1.4 Reconcile `apps/factory/manifest.json`: its entrypoint currently reads
      `/work/mission.json`. Either drop the argument so the environment is used,
      or keep it and have something write the file — not both, and the spec
      says which.

## 2. Startup failure reports as an app failure

- [x] 2.1 Validate endpoint and mission before any work that can exit; report a
      missing value by naming it.
- [x] 2.2 Test: no endpoint configured → the named variable appears in the
      output and the exit code is non-zero.
- [x] 2.3 Mutation: remove the validation and confirm 2.2 fails. Without this
      the test passes on the pre-existing `ValueError`, which is what made the
      failure look like an unreachable guest in the first place.

## 3. An image that runs the coordinator

- [x] 3.1 Fix the image so its command invokes `/usr/local/bin/barista-factory`.
      Both published tags currently carry
      `sh -c "echo 'coordinator ready'; exec sleep infinity"`, with the real
      binary present but never invoked.
- [x] 3.2 Keep the readiness contract: the manifest declares a `log_line`
      readiness of `coordinator ready`, and `__main__.py` already prints it. The
      image must not be the only thing printing it.
- [x] 3.3 Verify by `docker run` and by a Host API session that the same command
      runs in both.
- [x] 3.4 Publish and record the digest. The manifest pins by digest, so this is
      the step that makes the manifest installable.

## 4. The managed acceptance run

- [x] 4.1 New target, separate from the offline standalone flow, skipped with a
      stated reason when `BARISTA_HOST_API_ENDPOINT` is absent. Do not weaken
      the standalone guard to accommodate it.
- [x] 4.2 Install the manifest, create a coordinator session from it, run a
      mission to completion through the Host API alone.
- [x] 4.3 Assert what the local flow cannot: the coordinator is a *session*, its
      grant was minted by the provider, and workers are its children.
- [x] 4.4 Reap everything the run created, including on failure. The suite has
      leaked sessions before when acquisition failed part-way.

## 5. Pause and resume mid-mission

- [x] 5.1 A mission slow enough to still be running when the pause lands.
- [x] 5.2 Pause the coordinator's **session** while tasks are in flight, resume,
      assert the mission completes.
- [x] 5.3 Assert worker identity: an accepted attempt is served by the worker
      that already had it, never a second one. This is the actual content of
      "without creating a second worker for an already accepted attempt".
- [x] 5.4 Mutation: make the coordinator forget its state across resume and
      confirm 5.3 fails.

## 6. A mission longer than one grant lifetime

- [x] 6.1 A mission exceeding `lifetime - margin` (~720s against the measured
      900s). Marked slow; excluded from the default run.
- [x] 6.2 Assert `refreshes > 1`, `active` true, `authority_lost` null,
      `inactive_reason` null, mission `done`.
- [x] 6.3 Do **not** shorten the grant lifetime to make this cheap — see design
      D4. If a short-TTL variant is added for margin arithmetic, it is an
      addition, not a replacement.

## 7. Failure paths

- [x] 7.1 A task failing its `check` → task `failed`, and its dependent
      `blocked` rather than attempted.
- [x] 7.2 `max_attempts` honoured: a task failing twice with `max_attempts: 2`
      is attempted twice and no more.
- [x] 7.3 A failed worker is **not** reaped, so it remains available for
      forensics — the other half of *Artifacts SHALL be harvested before
      successful workers are reaped*, which currently has no test.
- [x] 7.4 Assert the receipt records the failure, and that a successful worker's
      artifacts are retrievable after it is deleted.

## 8. Close out

- [x] 8.1 `( cd apps/factory && uv run --extra test pytest -q )` and
      `( cd acceptance && uv run pytest -q )` green; the managed run green
      against beta with its result recorded here.
- [x] 8.2 `openspec validate --all --strict`.
- [x] 8.3 Record what the managed run measured — counts, `refreshes`, elapsed —
      the way the certification result is recorded in `capabilities.py`, so the
      next reader can tell evidence from intent.

### Recorded evidence — beta, 2026-08-27

Coordinator image `127.0.0.1:5000/barista-factory:0.4.2`, amd64, digest
`sha256:44481af3d5859cca0f63d513504488057ba33a9377ad2ea23bd025905d8a4c0d`.

- Default acceptance against beta: **4 passed**, 1 slow deselected, 84.93s
  (three managed cases plus the unchanged standalone case).
- Pause/resume: coordinator paused with one accepted worker in flight, resumed,
  completed with one receipt and exactly one `session.child_created` for that
  worker; 46.55s.
- Real-lifetime renewal: **1 passed** in 779.93s on `0.4.1` (the final `0.4.2`
  changes only the pre-network missing-configuration report). The persisted in-flight state
  observed `refreshes = 2`, `active = true`, `authority_lost = null`, and
  `inactive_reason = null` after more than 700s against the unmodified ~900s
  provider grant. The hold was then released and its receipt became durable.
- Failure paths: the failed check was attempted exactly twice, its dependent was
  never created, the failed worker remained readable, and the independent
  successful worker's receipt/output remained listable after that worker was
  deleted.
- Mutation checks: disabling retries failed
  `test_failure_retries_blocks_dependents_and_preserves_forensics` at
  `attempts == 2`; reaping the failed worker failed the same test at the
  forensics assertion; suppressing ticker status publication failed
  `test_ticker_persists_refresh_evidence_before_the_mission_finishes` at
  `refreshes >= 3`; forgetting a recovered running attempt failed
  `test_recovered_running_task_reuses_attempt_no_duplicate_worker` at
  `attempts == 1`; restoring the raw `Config.from_env()` exception failed
  `test_no_endpoint_is_reported_as_configuration_not_a_traceback` before its
  clean `SystemExit` assertion. Each source file was restored from a backup and
  the full package reran green.

## Not tasks

Scope exclusions, kept as bullets because a non-goal can never honestly be
ticked:

- A real agent worker image for `prompt` tasks. Needs an adapter baked in and a
  model credential; its own change.
- Reconciling tutorial 10's Claude-Code-skill factory (over `/v1`) with this
  one. Worth doing, not here.
- Any Cloud-side change. The provider is conformant for everything this needs.
