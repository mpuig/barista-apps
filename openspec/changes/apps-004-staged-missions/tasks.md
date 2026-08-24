## 1. Schema and load-time refusals

- [x] 1.1 `mission.schema.json`: add `depends_on` (array of task ids), `produces` (named outputs → paths in the task's own session), `consumes` (upstream output names → paths in this task's session), and `files` (planted path → content). All additive and optional; a mission using none of them must validate and behave exactly as today.
- [x] 1.2 `mission.py`: parse them onto `Task`, and refuse at load — a dependency cycle, an unknown task id, a self-edge, and a `consumes` naming an output no dependency `produces`. Name the offending task(s) and the offending id in the message; a refusal that says only "invalid graph" sends the operator reading JSON by hand. *Also refuses a `consumes` whose producer exists but is not depended on: that is a race dressed as a data flow, since nothing orders the producer first. Cycle detection is an iterative DFS — mission data comes from outside, so a deep chain must not become a `RecursionError`, which would be an error about the interpreter rather than about the mission.*
- [x] 1.3 A mission-level `strict_gates` (default `false`) that refuses at load any `check` naming a path which is neither planted nor consumed. **Opt-in, and why is the main finding of this change** (design D3): the always-on version refused this repo's own `missions/example.json`, whose `git -C /work diff --quiet` names `/work` as a *location* while the criterion is git's own notion of a clean tree — a sound check, indistinguishable by shape from a forged one. Refusing valid missions to catch invalid ones is the wrong trade for a portable app. The guarantee that needs no opt-in is the runtime one in §4.
- [x] 1.4 Decide and document how a check argv element is judged to "reference a path". Rule: absolute, or beginning `./`/`../`, compared after normalisation; **argv[0] exempt**, because the program comes from the image rather than the workspace and refusing an absolute interpreter path would break every check that names one. The limitation is stated in `_looks_like_a_path`'s docstring rather than hidden: a path buried in a shell string (`sh -c "node t.js"`) has no argv element beginning with a slash and escapes the rule. This guards the mission that forges a gate by accident — the case that has actually occurred — not one that sets out to.
- [x] 1.5 Tests: the shipped `example.json` still loads (`test_the_shipped_example_mission_still_loads` — the regression guard for over-strictness); each refusal fires with the offending name in its message; a valid graph loads; a 900-deep chain loads rather than exhausting the stack.

## 2. Scheduling

- [x] 2.1 `coordinator.py`: replace the single bulk submit with a ready-set loop — submit any task whose dependencies are all `ok` while a slot is free, re-evaluating on each completion (design D4). No level barriers.
- [x] 2.2 The ready set is **derived on every pass, never persisted** (design D5). A persisted ready set is a second record of a fact the task states already hold, and can disagree with them after a crash.
- [x] 2.3 `state.py`: a `blocked` task state, carrying the dependency that did not succeed. Distinct from `failed` (ran and did not pass) and from `pending` (will be attempted).
- [x] 2.4 **Decided the other way, deliberately: a mission with blocked tasks IS `done`.** The task suggested it should not be. Against: a mission with a *failed* task was already `done` before this change, so making blocked tasks withhold `done` would mean the same failure ends the mission differently depending on whether anything happened to depend on it — the state would describe the graph's shape rather than the run's outcome. `done` therefore keeps its existing meaning, "ran to a conclusion", and `summary()` carries the counts, with `blocked` present only when non-zero so a mission without dependencies reports byte-identically to before. `blocked_by` on the task names the cause, which is what sends the operator to it.
- [x] 2.5 Tests: B waits for A; independent tasks still run concurrently up to `concurrency`; a diamond (A → B, A → C, B+C → D) runs D exactly once and only after both; a failed A leaves B blocked and not failed; wall-clock is bounded by the critical path, not by per-level barriers (assert the concurrency property directly, not by timing).
- [x] 2.6 Test: a coordinator restarted mid-graph recomputes readiness from recovered states, does not re-run an `ok` task, and re-ensures a `running` task under its existing attempt (the existing idempotency-key property must be preserved, not re-derived).

## 3. Artifact passing

- [x] 3.1 On success, capture each declared `produces` path from the worker and register it on the durable coordinator scope, before the reap — reusing the existing harvest ordering rather than adding a second path (`_harvest_then_reap` already proves harvest precedes reap).
- [x] 3.2 Before a dependent's command runs, place its `consumes` content into its session. Transfer is worker → coordinator artifact → worker, never worker → worker (design D2): a direct copy would need both workers alive and would break the reap-on-success property.
- [x] 3.3 No second record of a task's output: `TaskState` keeps pointers (as `receipt_artifact_id` already is), not copies.
- [x] 3.4 Tests: B receives A's output; B still receives it when A's worker has already been reaped; content is byte-identical across the transfer (assert on the digest, using the SDK's existing canonical content addressing rather than a new one).

## 4. Planting

- [x] 4.1 Place `files` content into a worker's session after it is ensured and before its command runs.
- [x] 4.2 Planting is idempotent and survives restart: re-planting identical content into a re-ensured worker is a no-op, addressed by digest.
- [x] 4.3 **Re-assert planted content between the command and the check.** Design D3 promotes this from a nice-to-have to the change's actual defence, because it is the half that needs no opt-in and has no false positives: re-plant by digest (a no-op when untouched) so a worker that overwrote the criterion is judged against the mission's version anyway.
- [x] 4.4 Tests: planted content is present before the command runs; a worker that overwrites a planted path does not change what the check runs against.

## 5. The demo tells the truth

- [ ] 5.1 `demos/factory/missions/e2e-wave.json` in **barista-cloud**: `fizz` and `median` currently instruct the worker to write both the implementation and the test, and the check re-runs the worker's own test. Plant the tests instead. This is tracked here for sequencing and belongs to that repo.
- [x] 5.2 A mission in this repo demonstrating a real chain (produce → consume → planted check), as `missions/` data rather than as schema — the four-file review ceremony that motivated this change is expressible here and must stay a template, never a schema (design D1).
- [x] 5.3 `apps/factory/README.md`: what a dependency edge means, what planting is *for* (the criterion is not the worker's to write), and D3's stated limitation — this bounds self-marking, it does not eliminate tampering with what the check reads.

## 6. Gates

- [x] 6.1 `openspec validate --all --strict` — 13 passed, 0 failed.
- [x] 6.2 Every package CI runs, from its own directory: factory 43, contracts 54, conformance 21, providers/local 12, sdk 17, supply-chain OK.
- [x] 6.3 Mutation evidence — eight mutations, restored from backup each time, tree verified identical after. **One of them found a real hole rather than confirming a test**: making every task ready regardless of its dependencies broke *nothing*, because the outcome assertions could still pass by luck whenever a dependency happened to finish first. The change's central property had no test. `test_a_dependent_never_starts_before_its_dependency_has_finished` now asserts the interleaving directly, with a deliberately slow dependency so luck cannot supply the ordering, and it also asserts an independent task does *not* wait — otherwise it would pass equally against a serial scheduler. Re-run against the same mutation: caught.

## 7. Not in this change

- A gate that is itself an agent invocation (a reviewer worker on a read-only grant). Design D6 records why it waits: it needs the adapter boundary resolved first, since Factory's implementation is currently more coupled to one agent than `factory-app/spec.md:13` allows, and a reviewer is exactly the feature that turns that latent coupling into a contradiction.
- Renaming `check` into a `gate` union. Churn until there is a second arm to justify the union; `check` becomes its `command` arm when the reviewer lands.
- Mission lineage (a mission's output becoming another mission, with a recorded parent). Wanted, and a separate change.
