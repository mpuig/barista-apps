## 1. Schema and load-time refusals

- [x] 1.1 `mission.schema.json`: add `depends_on` (array of task ids), `produces` (named outputs → paths in the task's own session), `consumes` (upstream output names → paths in this task's session), and `files` (planted path → content). All additive and optional; a mission using none of them must validate and behave exactly as today.
- [x] 1.2 `mission.py`: parse them onto `Task`, and refuse at load — a dependency cycle, an unknown task id, a self-edge, and a `consumes` naming an output no dependency `produces`. Name the offending task(s) and the offending id in the message; a refusal that says only "invalid graph" sends the operator reading JSON by hand. *Also refuses a `consumes` whose producer exists but is not depended on: that is a race dressed as a data flow, since nothing orders the producer first. Cycle detection is an iterative DFS — mission data comes from outside, so a deep chain must not become a `RecursionError`, which would be an error about the interpreter rather than about the mission.*
- [x] 1.3 A mission-level `strict_gates` (default `false`) that refuses at load any `check` naming a path which is neither planted nor consumed. **Opt-in, and why is the main finding of this change** (design D3): the always-on version refused this repo's own `missions/example.json`, whose `git -C /work diff --quiet` names `/work` as a *location* while the criterion is git's own notion of a clean tree — a sound check, indistinguishable by shape from a forged one. Refusing valid missions to catch invalid ones is the wrong trade for a portable app. The guarantee that needs no opt-in is the runtime one in §4.
- [x] 1.4 Decide and document how a check argv element is judged to "reference a path". Rule: absolute, or beginning `./`/`../`, compared after normalisation; **argv[0] exempt**, because the program comes from the image rather than the workspace and refusing an absolute interpreter path would break every check that names one. The limitation is stated in `_looks_like_a_path`'s docstring rather than hidden: a path buried in a shell string (`sh -c "node t.js"`) has no argv element beginning with a slash and escapes the rule. This guards the mission that forges a gate by accident — the case that has actually occurred — not one that sets out to.
- [x] 1.5 Tests: the shipped `example.json` still loads (`test_the_shipped_example_mission_still_loads` — the regression guard for over-strictness); each refusal fires with the offending name in its message; a valid graph loads; a 900-deep chain loads rather than exhausting the stack.

## 2. Scheduling

- [ ] 2.1 `coordinator.py`: replace the single bulk submit with a ready-set loop — submit any task whose dependencies are all `ok` while a slot is free, re-evaluating on each completion (design D4). No level barriers.
- [ ] 2.2 The ready set is **derived on every pass, never persisted** (design D5). A persisted ready set is a second record of a fact the task states already hold, and can disagree with them after a crash.
- [ ] 2.3 `state.py`: a `blocked` task state, carrying the dependency that did not succeed. Distinct from `failed` (ran and did not pass) and from `pending` (will be attempted).
- [ ] 2.4 A mission that ends with blocked tasks is not `done`. Decide what it is called and make the summary say how many were blocked and by what — the existing `lost_authority` precedent is the model: the operator must be sent to the cause, not to the symptom.
- [ ] 2.5 Tests: B waits for A; independent tasks still run concurrently up to `concurrency`; a diamond (A → B, A → C, B+C → D) runs D exactly once and only after both; a failed A leaves B blocked and not failed; wall-clock is bounded by the critical path, not by per-level barriers (assert the concurrency property directly, not by timing).
- [ ] 2.6 Test: a coordinator restarted mid-graph recomputes readiness from recovered states, does not re-run an `ok` task, and re-ensures a `running` task under its existing attempt (the existing idempotency-key property must be preserved, not re-derived).

## 3. Artifact passing

- [ ] 3.1 On success, capture each declared `produces` path from the worker and register it on the durable coordinator scope, before the reap — reusing the existing harvest ordering rather than adding a second path (`_harvest_then_reap` already proves harvest precedes reap).
- [ ] 3.2 Before a dependent's command runs, place its `consumes` content into its session. Transfer is worker → coordinator artifact → worker, never worker → worker (design D2): a direct copy would need both workers alive and would break the reap-on-success property.
- [ ] 3.3 No second record of a task's output: `TaskState` keeps pointers (as `receipt_artifact_id` already is), not copies.
- [ ] 3.4 Tests: B receives A's output; B still receives it when A's worker has already been reaped; content is byte-identical across the transfer (assert on the digest, using the SDK's existing canonical content addressing rather than a new one).

## 4. Planting

- [ ] 4.1 Place `files` content into a worker's session after it is ensured and before its command runs.
- [ ] 4.2 Planting is idempotent and survives restart: re-planting identical content into a re-ensured worker is a no-op, addressed by digest.
- [ ] 4.3 **Re-assert planted content between the command and the check.** Design D3 promotes this from a nice-to-have to the change's actual defence, because it is the half that needs no opt-in and has no false positives: re-plant by digest (a no-op when untouched) so a worker that overwrote the criterion is judged against the mission's version anyway.
- [ ] 4.4 Tests: planted content is present before the command runs; a worker that overwrites a planted path does not change what the check runs against.

## 5. The demo tells the truth

- [ ] 5.1 `demos/factory/missions/e2e-wave.json` in **barista-cloud**: `fizz` and `median` currently instruct the worker to write both the implementation and the test, and the check re-runs the worker's own test. Plant the tests instead. This is tracked here for sequencing and belongs to that repo.
- [ ] 5.2 A mission in this repo demonstrating a real chain (produce → consume → planted check), as `missions/` data rather than as schema — the four-file review ceremony that motivated this change is expressible here and must stay a template, never a schema (design D1).
- [ ] 5.3 `apps/factory/README.md`: what a dependency edge means, what planting is *for* (the criterion is not the worker's to write), and D3's stated limitation — this bounds self-marking, it does not eliminate tampering with what the check reads.

## 6. Gates

- [ ] 6.1 `openspec validate --all --strict`.
- [ ] 6.2 `( cd apps/factory && uv run --extra test pytest -q )`, and the packages CI runs — from each package's own directory, since there is no repo-root test run.
- [ ] 6.3 Mutation evidence, both directions: remove the cycle refusal and a test fails naming the cycle; remove the check-path refusal and a test fails naming a forged gate; make every task ready regardless of dependencies and the ordering test fails; make no task ready and the concurrency test fails. Back up before mutating, restore from the backup, never leave a mutation in the tree.

## 7. Not in this change

- A gate that is itself an agent invocation (a reviewer worker on a read-only grant). Design D6 records why it waits: it needs the adapter boundary resolved first, since Factory's implementation is currently more coupled to one agent than `factory-app/spec.md:13` allows, and a reviewer is exactly the feature that turns that latent coupling into a contradiction.
- Renaming `check` into a `gate` union. Churn until there is a second arm to justify the union; `check` becomes its `command` arm when the reviewer lands.
- Mission lineage (a mission's output becoming another mission, with a recorded parent). Wanted, and a separate change.
