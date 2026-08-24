## 1. Schema and load-time refusals

- [ ] 1.1 `mission.schema.json`: add `depends_on` (array of task ids), `produces` (named outputs → paths in the task's own session), `consumes` (upstream output names → paths in this task's session), and `files` (planted path → content). All additive and optional; a mission using none of them must validate and behave exactly as today.
- [ ] 1.2 `mission.py`: parse them onto `Task`, and refuse at load — a dependency cycle, an unknown task id, a self-edge, and a `consumes` naming an output no dependency `produces`. Name the offending task(s) and the offending id in the message; a refusal that says only "invalid graph" sends the operator reading JSON by hand.
- [ ] 1.3 `mission.py`: refuse at load a `check` whose argv references a path that is neither planted nor consumed (design D3). Load-time, not runtime: a mission that cannot verify anything must be unrunnable, and saying so after the work ran reads as "the check broke" rather than "this mission never had a gate".
- [ ] 1.4 Decide and document how a check argv element is judged to "reference a path" — this is the rule's whole surface, and a sloppy answer either refuses valid missions or lets a forged gate through. Prefer an explicit, conservative rule (an element that is absolute, or begins `./`/`../`, or matches a declared workdir-relative path) over a heuristic that tries to be clever about shell strings, and state the limitation rather than hiding it: a check that goes through `sh -c` can hide a path from any such rule, and that is a documented hole, not a solved one.
- [ ] 1.5 Tests: every existing mission in the repo still loads and produces an identical `Mission`; each refusal fires with the offending name in the message; a valid graph loads.

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
- [ ] 4.3 Tests: planted content is present before the command runs; a worker that overwrites a planted path does not change what the check runs against, **or** — if that cannot be enforced with the primitives available — the limitation is stated plainly in the README and this task is left unchecked rather than ticked on a weaker property.

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
