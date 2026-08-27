# Tasks

Ordered so each step is testable before the next depends on it. §1–§3 are what
unblock an in-session run; §4 onward is what that run then proves.

## 1. Mission delivery

- [ ] 1.1 Make `$BARISTA_FACTORY_MISSION` the documented delivery mechanism in
      `apps/factory/README.md`, alongside the existing path argument.
- [ ] 1.2 Test: a mission in the environment runs with no file present.
- [ ] 1.3 Test: an explicit path that does not exist fails **naming the path**,
      while `$BARISTA_FACTORY_MISSION` holds a different valid mission. This
      pins `_load_mission`'s existing precedence against a future "helpful"
      fallback.
- [ ] 1.4 Reconcile `apps/factory/manifest.json`: its entrypoint currently reads
      `/work/mission.json`. Either drop the argument so the environment is used,
      or keep it and have something write the file — not both, and the spec
      says which.

## 2. Startup failure reports as an app failure

- [ ] 2.1 Validate endpoint and mission before any work that can exit; report a
      missing value by naming it.
- [ ] 2.2 Test: no endpoint configured → the named variable appears in the
      output and the exit code is non-zero.
- [ ] 2.3 Mutation: remove the validation and confirm 2.2 fails. Without this
      the test passes on the pre-existing `ValueError`, which is what made the
      failure look like an unreachable guest in the first place.

## 3. An image that runs the coordinator

- [ ] 3.1 Fix the image so its command invokes `/usr/local/bin/barista-factory`.
      Both published tags currently carry
      `sh -c "echo 'coordinator ready'; exec sleep infinity"`, with the real
      binary present but never invoked.
- [ ] 3.2 Keep the readiness contract: the manifest declares a `log_line`
      readiness of `coordinator ready`, and `__main__.py` already prints it. The
      image must not be the only thing printing it.
- [ ] 3.3 Verify by `docker run` and by a Host API session that the same command
      runs in both.
- [ ] 3.4 Publish and record the digest. The manifest pins by digest, so this is
      the step that makes the manifest installable.

## 4. The managed acceptance run

- [ ] 4.1 New target, separate from the offline standalone flow, skipped with a
      stated reason when `BARISTA_HOST_API_ENDPOINT` is absent. Do not weaken
      the standalone guard to accommodate it.
- [ ] 4.2 Install the manifest, create a coordinator session from it, run a
      mission to completion through the Host API alone.
- [ ] 4.3 Assert what the local flow cannot: the coordinator is a *session*, its
      grant was minted by the provider, and workers are its children.
- [ ] 4.4 Reap everything the run created, including on failure. The suite has
      leaked sessions before when acquisition failed part-way.

## 5. Pause and resume mid-mission

- [ ] 5.1 A mission slow enough to still be running when the pause lands.
- [ ] 5.2 Pause the coordinator's **session** while tasks are in flight, resume,
      assert the mission completes.
- [ ] 5.3 Assert worker identity: an accepted attempt is served by the worker
      that already had it, never a second one. This is the actual content of
      "without creating a second worker for an already accepted attempt".
- [ ] 5.4 Mutation: make the coordinator forget its state across resume and
      confirm 5.3 fails.

## 6. A mission longer than one grant lifetime

- [ ] 6.1 A mission exceeding `lifetime - margin` (~720s against the measured
      900s). Marked slow; excluded from the default run.
- [ ] 6.2 Assert `refreshes > 1`, `active` true, `authority_lost` null,
      `inactive_reason` null, mission `done`.
- [ ] 6.3 Do **not** shorten the grant lifetime to make this cheap — see design
      D4. If a short-TTL variant is added for margin arithmetic, it is an
      addition, not a replacement.

## 7. Failure paths

- [ ] 7.1 A task failing its `check` → task `failed`, and its dependent
      `blocked` rather than attempted.
- [ ] 7.2 `max_attempts` honoured: a task failing twice with `max_attempts: 2`
      is attempted twice and no more.
- [ ] 7.3 A failed worker is **not** reaped, so it remains available for
      forensics — the other half of *Artifacts SHALL be harvested before
      successful workers are reaped*, which currently has no test.
- [ ] 7.4 Assert the receipt records the failure, and that a successful worker's
      artifacts are retrievable after it is deleted.

## 8. Close out

- [ ] 8.1 `( cd apps/factory && uv run --extra test pytest -q )` and
      `( cd acceptance && uv run --extra test pytest -q )` green; the managed
      run green against beta with its result recorded here.
- [ ] 8.2 `openspec validate --all --strict`.
- [ ] 8.3 Record what the managed run measured — counts, `refreshes`, elapsed —
      the way the certification result is recorded in `capabilities.py`, so the
      next reader can tell evidence from intent.

## Not tasks

Scope exclusions, kept as bullets because a non-goal can never honestly be
ticked:

- A real agent worker image for `prompt` tasks. Needs an adapter baked in and a
  model credential; its own change.
- Reconciling tutorial 10's Claude-Code-skill factory (over `/v1`) with this
  one. Worth doing, not here.
- Any Cloud-side change. The provider is conformant for everything this needs.
