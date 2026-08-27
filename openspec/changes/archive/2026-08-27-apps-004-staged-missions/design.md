# apps-004 — design

## The mechanism, confirmed

Read before designing, so the claims below are about the code and not about the
documentation:

- `apps/factory/mission.schema.json` — task properties are exactly `id`,
  `command`, `prompt`, `check`, `collect`, `env`, `workdir`. `required: ["id"]`.
  No edges, no inputs, no outputs.
- `barista_app_factory/coordinator.py::_run_inside_credential` — every pending
  task is submitted to one `ThreadPoolExecutor(max_workers=concurrency)` in a
  single loop, then awaited with `as_completed`. Order is arbitrary; there is no
  place where one task's completion could release another.
- `coordinator.py::run_task` — the check is
  `self.client.exec(worker.id, task.check)`. Same `worker.id` the task ran in.
- `coordinator.py::_harvest_then_reap` — the receipt is registered as an artifact
  on the coordinator session **before** `delete_session` on the worker, and the
  docstring says why: "a retrievable receipt proves harvest completed before the
  reap". So durable per-task output already exists and already outlives its
  worker. This change consumes that, it does not invent it.
- `openspec/specs/factory-app/spec.md:21` — "Factory SHALL persist mission, **task
  graph**, worker handles, attempts, deadlines, status, receipts, and artifact
  references". Ratified. Unimplemented.

## D1 — Edges on tasks, not stages in the schema

A staged pipeline can be modelled two ways: name the stages (`spec`, `plan`,
`implement`, `review`) or give tasks edges and let a stage be whatever a task
does. This takes the second.

Naming stages puts one organisation's process into a portable app's schema.
Factory's first ratified requirement is that it "SHALL not … assume a specific
agent adapter" and SHALL run the same mission on a local and a Cloud provider;
a schema whose vocabulary is one company's approval ceremony fails the spirit of
that even when it passes the letter. It also ages badly in a way edges do not:
every organisation that wants a fifth stage, or a different third one, needs a
schema change.

Edges cost one field and express every chain anyone can name, including the
four-file chain that motivated this work. The concrete form that chain takes is a
*mission template* shipped alongside `example.json` — data, versionable by
whoever owns the process, and not a constraint on anyone who wants a different
one.

The second reason is that barista already runs a stricter version of that
ceremony on itself. This repository's own workflow is proposal → design → tasks
→ spec deltas, with `openspec validate --strict`, ADDED/MODIFIED/REMOVED
semantics, and MODIFIED requiring verbatim restatement of ratified text, checked
programmatically. Encoding a *weaker* chain into the mission schema and calling
it rigour would be a downgrade dressed as a feature.

## D2 — Artifact passing is what makes an edge worth having

`depends_on` alone orders execution. Ordering without transfer is close to
useless for the cases that motivated it: "implement from the spec" needs the
spec, and each task runs in its own fresh session, so ordering alone gives the
implementer an empty disk and a promise that something happened elsewhere.

So an edge carries content. A task declares `produces` (paths in its own session
to capture on success) and a dependent declares `consumes` (which upstream
outputs to place, and where). The transfer goes through the durable coordinator
scope that already exists, and the direction of that flow matters: it is
worker → coordinator artifact → worker, never worker → worker. A direct copy
would need two workers alive at once, which breaks the reap-on-success property
the harvest ordering exists to guarantee, and would make the transfer's integrity
depend on a session that is already gone.

**The single-source-of-truth rule is unchanged.** The receipt and the artifact
registry remain the record; nothing here adds a second place where a task's
output is described. `state.py`'s `TaskState` gains no result copy — it already
holds `receipt_artifact_id`, a *pointer*, which is precisely the shape bar-060 D3
argued for and the reason `status.json` was deleted.

## D3 — Two mechanisms: re-assertion always, the argv rule on request

This is the core of the change, and the first draft of it was wrong in a way
worth recording, because the wrong version is the obvious one.

**The draft.** Every argv element of `check` that names a path must be planted
or consumed; anything else is a load-time refusal. It catches the motivating bug
exactly — `node /work/fizz.test.js` where the worker wrote `fizz.test.js`.

**Why it fails.** An argv element that names a path is not necessarily the
criterion. This repo's own `missions/example.json` checks with
`git -C /work diff --quiet`: `/work` is the *location* to inspect, and the
criterion is git's own notion of a clean tree. That is a sound check, and the
rule refused it — caught by running the rule against the repo's own example
rather than only against the case it was designed for. Syntactically
`git -C /work …` and `node /work/its-own-test.js` are the same shape, and no
amount of cleverness separates them without a model of what each program does
with its arguments. For a portable app, refusing valid missions to catch invalid
ones is the wrong trade.

**What actually defends, and needs no opt-in.** Planted content is **re-asserted
immediately before the check runs**. The coordinator re-plants each `files` entry
(addressed by digest, so it is a no-op when untouched) between the task's command
and its check. A worker that overwrote the planted criterion has the mission's
version restored before it is judged.

This is strictly stronger than the argv rule and has no false positives: it makes
no claim about which argv element is the criterion, it just guarantees that
planted content *is* the mission's. It also closes a hole the argv rule left open
and that the first draft of this design admitted it could not close — a worker
overwriting the planted file. The rule that survives is the runtime one.

**The argv rule survives as opt-in.** `strict_gates: true` on the mission turns
it on for every task. A mission that wants "no check in me reads anything the
checked task could have written" can assert it and have it enforced at load,
before a worker exists. Off by default, so `example.json` and every mission like
it keeps working. argv[0] is exempt under the rule: it is the program being run —
`node`, `pytest`, `/usr/local/bin/rspec` — which comes from the image, not the
workspace, and is no more the subject of the check than the shell is.

**What neither claims.** A worker can still defeat a planted check by tampering
with what the check *reads* rather than the criterion itself — replacing the
module under test with a stub that satisfies it. Nothing here closes that, and it
should not be implied. The honest claim is narrower and still worth having: **the
criterion is fixed by someone other than the party being judged**, which is the
difference between evidence and self-assessment. And under `strict_gates`, a
check that names a path burying it in a shell string (`sh -c "node t.js"`) has no
argv element beginning with a slash and escapes the rule — a documented hole, not
a solved one.

**Prior art in this repo, deliberately mirrored.** apps-003's conformance work
established that a test which cannot fail is not a test, by writing dishonest
provider doubles and proving the suite catches them. This is the same standard
applied to missions instead of providers — and the reason the over-strict draft
was caught is that it was run against a mission it was not designed for.

## D4 — Scheduling: a ready-set loop, not a topological batch

The obvious implementation runs the graph in levels: compute a topological
order, run level 0 to completion, then level 1. That introduces a barrier per
level, so one slow task in level 0 idles every worker until it finishes.

Instead the coordinator keeps a ready set — tasks whose dependencies are all
`ok` — and submits from it whenever a slot is free, re-evaluating on each
completion. A task with no dependencies is ready immediately, so a mission
without edges behaves exactly as today, and wall-clock is bounded by the critical
path rather than by the sum of the slowest task per level.

**A failed dependency does not fail its dependents; it makes them
unreachable.** They are recorded `blocked`, naming the dependency. Not `failed`,
because they never ran and reporting them as failures sends someone to debug work
that never happened — the same reasoning `_blame_the_operator` already applies
when it returns an unattempted task to `pending` rather than marking it failed.
Blocked is distinct from pending: pending will be attempted, blocked will not.

## D5 — Restart safety is inherited, not re-derived

Recovery already works by re-reading `MissionState` and re-ensuring workers under
stable idempotency keys (`<mission>:<task>:attempt-<n>`). Nothing here changes
that, and nothing here may weaken it.

The ready set is **derived from task states on every pass**, never persisted. A
persisted ready set is a second record of the same fact and can disagree with the
task states after a crash — the failure mode this codebase has already paid for
once. A coordinator that restarts mid-mission recomputes readiness from the
states it recovers and continues; a task already `ok` is not re-run, which the
existing `run_task` guard covers.

Planting is idempotent for the same reason and keyed the same way: re-planting
identical content into a re-ensured worker is a no-op, and the content is
addressed by digest.

## D6 — Not in this change: the gate that is an agent

The most interesting gate is a *reviewer*: a second worker, given the first's
output and a read-only grant, asked whether the work is acceptable. Barista is
unusually well-placed for it — "read-only" would be an authorization fact
enforced by the provider at the gateway before node dispatch, not a convention
inside one process that the reviewing agent could ignore.

It waits, for a reason that is about this repo's own honesty rather than about
difficulty. `factory-app/spec.md` says Factory "SHALL not … assume a specific
agent adapter", while the worker image bakes one agent and the coordinator shells
it directly. A reviewer needs a *differently-invoked* agent than the implementer,
so it is precisely the feature that would turn that latent coupling into a
visible contradiction. Fixing the adapter boundary first, then adding the
reviewer, is the order that leaves the spec true at every step.

`gate` as a discriminated union (`command` | `invocation` | `escalate`) is the
shape it should take when it lands. `check` stays as it is here rather than being
migrated pre-emptively: renaming a field for a feature that does not exist yet
would be churn, and the union can absorb `check` as its `command` arm when there
is a second arm to justify the union.
