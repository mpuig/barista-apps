## Why

**A mission is a flat bag of independent tasks, and its gates are written by the
things they judge.** Both are visible in the shipped code, and together they cap
what Factory can honestly be used for.

*The flat bag.* `openspec/specs/factory-app/spec.md` already ratifies that the
coordinator persists "mission, **task graph**, worker handles, attempts,
deadlines, status, receipts, and artifact references". There is no task graph.
`mission.schema.json`'s task properties are `id, command, prompt, check, collect,
env, workdir` — no edges — and `coordinator.py` submits every pending task to one
`ThreadPoolExecutor` bounded by `concurrency`. Every task starts with the same
empty world and nothing any other task produced. So a mission can express "run
these four things at once" and cannot express "write the spec, then implement it,
then review the implementation" — which is most real work.

*The forged gate.* `check` is the coordinator's independent verification, and it
is real: it catches an agent that replies DONE over a failing test, which is a
failure mode that actually occurs. But look at where it runs. In
`coordinator.py` the check is `self.client.exec(worker.id, task.check)` — the
worker's **own session**, on files that worker wrote. The shipped demo mission
makes the consequence concrete: `e2e-wave.json`'s `fizz` task tells the agent to
write `fizz.js` *and* `fizz.test.js`, and the check is `node /work/fizz.test.js`.
The coordinator re-runs a test its subject authored. `median` has the same shape.

That is not a sandbox failure and no sandbox fixes it. A worker gets a fresh
microVM, ephemeral disk, allowlisted egress and a scoped short-lived grant, and
that isolation is doing its job — it answers *containment*. It does not answer
*self-marking*, because the agent and the test are inside the same VM. A test the
worker could have written to pass is not evidence, and a gate whose subject
authored it is not a gate.

These two are one problem, not two. Sequencing without trustworthy gates is a
pipeline that advances on the previous stage's own say-so, which is worse than no
sequencing at all: it launders an unchecked result into an input the next stage
treats as given. So they ship together.

## What Changes

- **`depends_on`** on a task: the ids that must reach `ok` before it may start.
  The coordinator schedules in topological order, still bounded by `concurrency`,
  so independent tasks keep running in parallel and only genuine edges serialise.
  A cycle, a missing id, or a self-edge is a load-time refusal.
- **Artifact passing between tasks.** A task declares what it `produces`; a
  dependent declares what it `consumes`, and receives it in its own fresh
  session. Without this, `depends_on` orders work that still cannot hand anything
  along, which is sequencing in name only. The bytes already exist — Factory
  registers each task's output as an artifact on the durable coordinator scope
  *before* reaping the worker, precisely so they outlive it.
- **`files`: planted inputs, re-asserted before the check.** Content placed into
  a worker's session before its command runs, and re-placed (by digest, so a
  no-op when untouched) between the command and the check. A worker that
  overwrote the criterion has the mission's version restored before it is judged.
- **`strict_gates`: an opt-in load-time rule** that a check may name only planted
  or consumed paths. Opt-in because an argv path may be a *location* rather than
  a criterion — `git -C /work diff --quiet` is sound and indistinguishable, by
  shape, from a forged gate. Design D3 records that the always-on version of this
  rule refused this repo's own example mission.

Everything stays inside `apps/factory` and its mission schema. No Host API
change, no manifest change, no new capability.

## Capabilities

### New Capabilities
<!-- None. This is Factory implementing a `factory-app` requirement it already
     carries ("task graph") and closing a gap in one it already claims
     (independent verification). -->

### Modified Capabilities
- `factory-app`: missions gain dependency edges, artifact passing between tasks,
  and gates whose inputs the judged worker cannot author.

## Impact

- **Schema**: four additive task properties (`depends_on`, `produces`,
  `consumes`, `files`) and one mission property (`strict_gates`). Every existing
  mission stays valid and behaves identically — a mission with no `depends_on` is
  one stage of everything, which is exactly today's semantics, and `strict_gates`
  defaults off. `example.json` keeps working unchanged, and there is now a test
  asserting it does, because the first draft of the gate rule broke it.
- **Coordinator**: scheduling becomes a ready-set loop instead of one bulk
  submit. Restart behaviour is unchanged and still keyed on the same idempotency
  keys; a task whose dependency is not yet `ok` is simply not ready, which is a
  state the existing `pending` already expresses.
- **The demo tells the truth after this.** `e2e-wave.json`'s `fizz` and `median`
  get planted tests, so the check stops being a formality. That is the point of
  the change and it should be visible in the mission everyone reads first.
- **Portability, deliberately preserved.** These are generic mechanisms —
  edges, artifacts, planted files — not one organisation's process. Factory's
  first ratified requirement is that it not assume a specific agent adapter or
  shape, and a schema that hard-coded a particular review ceremony
  (`intent.md` → `spec.md` → `plan.md`) would violate it. Such a chain is
  expressible *on top* of this change as an ordinary mission, and belongs in a
  mission template, never in the schema.
- **Not in this change**: a gate that is itself an agent invocation (a reviewer
  worker holding a read-only grant). It is the natural next step and the design
  records why it waits — it needs the adapter-boundary question resolved, and
  Factory's implementation is currently more coupled to one agent than its own
  spec allows.
