# apps/factory

A durable **software-factory** coordinator as a portable Barista app: it fans a
mission across worker sessions, supervises them, harvests each successful
worker's receipt and artifacts **before** reaping it, keeps failed workers for
forensics, and reconstructs its state idempotently after a restart.

Ported from `barista-cloud`'s `demos/factory` to run through **only** the Host
API/SDK — no privileged node contract, no provider database/bucket, no
Cloud-specific shape.

- **Manifest:** [`manifest.json`](manifest.json)
- **Mission schema:** [`mission.schema.json`](barista_app_factory/mission.schema.json)
- **Example mission:** [`missions/example.json`](missions/example.json)
- **Spec:** `openspec/changes/apps-001-portable-agent-apps/specs/factory-app/spec.md`

## Run a mission

```bash
cd apps/factory
uv run barista-factory run missions/example.json \
  --endpoint http://localhost:8088 --state /work/mission-state.json
```

The same mission runs unchanged against Barista Cloud by changing only the
endpoint/token — Factory branches on discovered capabilities, never on provider
name.

A coordinator running **as a session** receives the same JSON atomically at
creation through `$BARISTA_FACTORY_MISSION`; no file has to exist before the
workload starts. The positional path remains for an operator invoking the
binary directly. An explicitly named path that is unreadable is an error and
never falls back to the environment.

When launched through the shared App Run protocol, Factory also receives the
provider-reserved `$BARISTA_APP_SESSION_ID`. It uses that owning session as the
durable coordinator scope rather than creating a duplicate. At terminal
completion it writes canonical result bytes to
`/tmp/barista/app-run-result.json` and registers `app-run-result.json` on that
scope. A runner can therefore verify and persist the result before optionally
cleaning up the coordinator; direct `barista-factory run` invocation keeps its
existing mission output unchanged.

## Repository software changes

Factory also declares the typed `software-change` coordinator operation. Its
run binds one `sh.barista.git.repository` workspace and either a bounded local
text/specification or `com.github.issue` objective. The objective is inert
content: it cannot select a repository, add credentials, alter checks, request a
delivery, or choose a publication target.

The coordinator resolves the repository once, records its exact commit, then
gives each isolated worker a separate clone checked out detached at that same
commit. Workers never share a writable tree. Each successful worker yields a
bounded, secret-scanned binary Git patch. Factory copies those patches and
receipts into the owning session before deleting successful worker compute;
failed workers remain available for bounded forensics.

Factory applies the patches to a fresh integration checkout. Before running the
acceptance argv, it reasserts any coordinator-owned acceptance files from the
validated run envelope. The check subprocess gets a minimal environment without
the Host API grant or forge credentials and runs as the image's unprivileged
`nobody` account. A worker's attempt to weaken a criterion is therefore not
accepted as evidence. Failed integration preserves worker patches and receipts
and cannot publish.

A draft pull request is created through a `ForgeAdapter` only if the run
explicitly declares the `change` delivery and independent acceptance succeeds.
The draft records the objective revision, exact base and resulting head,
app/workload identity, integration-check receipt, integrated patch, and worker
receipt digests. A declared delivery with `options.executor="runner"` instead
returns the verified patch plus a `pending_deliveries.change` request identity;
Factory does not contact the forge or claim that side effect completed. This
keeps forge authority in a trusted trigger such as the GitHub Factory demo.
Without a delivery, the same verified run returns only its local patch, optional
branch, and canonical result.

The worker app must provide Git and a long-running exec-capable workload. A
public GitHub repository works with the shipped network allowlist. Factory uses
the SDK's concrete GitHub adapter for issue resolution and, when an explicit
delivery and `GITHUB_TOKEN` are present, token-safe draft publication.
Credentialed private-repository acquisition remains refused by this reference
operation rather than exposing a token in argv or a clone URL.

## How mission coordination behaves

- **One durable coordinator owns mission state** — task graph, worker handles,
  attempts, and receipts persist after every transition. A restart reconstructs
  the mission and re-ensures workers with a stable idempotency key rather than
  duplicating an accepted task.
- **Harvest before reap** — for every successful worker, Factory registers the
  result receipt (and declared artifacts) on the durable coordinator session
  **before** deleting the worker. A retrievable receipt proves harvest completed
  before the reap.
- **Failed workers survive** — Factory honors `max_attempts`, registers the
  final failed receipt, and leaves the worker in place for bounded forensics
  instead of deleting it.
- **Tasks may depend on tasks** — `depends_on` names the tasks that must succeed
  first. Scheduling is a ready set, not levels: a slot is refilled the moment any
  task completes, so wall-clock follows the critical path rather than the sum of
  each level's slowest task, and a mission with no edges runs exactly as it did
  before edges existed. A task whose dependency failed is reported **blocked**,
  naming the dependency — not failed, because it never ran, and not pending,
  because it never will.
- **A stage hands its output to the next** — a task declares `produces`, a
  dependent declares `consumes`, and the content arrives in the dependent's own
  session before its command runs. The route is worker → coordinator → worker,
  never worker to worker: a direct copy would need both workers alive at once,
  which is exactly what reap-on-success gives up.

## Gates the worker cannot forge

`check` is Factory's independent verification. It catches an agent that reports
DONE over a failing test — a real failure mode. But a check is only independent
if the *criterion* did not come from the thing being judged, and a microVM does
not give you that: the isolation answers **containment**, not **self-marking**,
because the agent and the test are inside the same VM.

Two mechanisms, and it is worth knowing which is which:

- **Planting, re-asserted before the check.** `files` places the mission's own
  content into the worker's session before the command runs, and places it again
  between the command and the check. A worker that overwrote the criterion is
  judged against the mission's copy. This is always on, costs nothing when the
  content is untouched, and needs no cooperation from the mission author beyond
  planting the file.
- **`strict_gates`** (opt-in, mission-level) additionally refuses **at load** any
  check naming a path that is neither planted nor consumed. Opt-in on purpose: an
  argv path is not always the criterion. `git -C /work diff --quiet` names
  `/work` as the *place to look* while the criterion is git's own notion of a
  clean tree — sound, and indistinguishable by shape from a forged gate. An
  always-on version of this rule refused this repo's own `missions/example.json`.

**What this does not claim.** A worker can still defeat a planted check by
tampering with what the check *reads* rather than the criterion itself —
replacing the module under test with a stub that satisfies it. And under
`strict_gates`, a path buried in a shell string (`sh -c "node t.js"`) has no argv
element beginning with a slash, so it escapes the rule. The honest claim is
narrower and still worth having: **the criterion is fixed by someone other than
the party being judged**, which is the difference between evidence and
self-assessment.

`missions/staged.json` is the worked example: an intent is planted, a spec stage
produces one, an implementation stage consumes it and is judged by a planted
acceptance suite it was told not to edit, and a documentation stage consumes
both. Note that the four-file review ceremonies people build on top of this
(`intent.md → spec.md → plan.md → diff`) are **mission data**, not schema — the
schema knows only about edges, outputs and planted files, which is what keeps
Factory portable.
- **Bounded delegation** — a mission declares allowed adapters, secret
  *references*, egress, concurrency, attempts, deadline, and budgets. Workers
  receive a strictly narrower grant (no child-session creation) and see secret
  references, never raw values.
- **The provider is the only minter** — `manifest.json` declares what a worker
  receives (`permissions.child_sessions.actions`) and that a worker may not
  create sessions of its own (`allow_descendants: false`). Factory asks for a
  worker session; the *provider* mints that worker's grant from the same
  manifest. Factory never handles a credential it did not receive itself, and
  the coordinator's own credential is a `grant://` reference, not a tenant key.

## The coordinator's own credential

A delegated grant is injected into a session once, as an environment variable a
running process cannot rewrite, and it expires — after fifteen minutes on the
reference provider, against a default task timeout of 3600 seconds. So Factory
refreshes its own credential while a mission runs, through
`POST /v1alpha1/grants/refresh`, whenever the provider advertises
`grants.delegated`. On a provider that does not, or with a credential that is not
a refreshable grant, the keeper stays inactive and records why: the mission runs
exactly as it did before, bounded by that credential's life.

**The margin is 20% of the observed lifetime, floored at 60s and capped at half
the lifetime** — 180 seconds for a 900-second grant.

- *A fraction, not a constant*, so the margin scales with whatever lifetime the
  provider chose. A number tuned for fifteen minutes is wrong for one minute, and
  a portable app should not carry an arbitrary one.
- *At least a minute*, because the decision compares Factory's clock against a
  timestamp the provider produced. Hosts that are not well synchronised differ by
  tens of seconds, and refreshing at the last moment against a clock slightly
  ahead of yours is a lockout, not a near miss — a lapsed grant cannot be
  refreshed.
- *At most half the lifetime*, so a short-lived grant is not rotated the instant
  it arrives, over and over.

Freshness is checked at each task boundary *and* by a background ticker, because
a task with an hour's timeout outlives a fifteen-minute grant without the
coordinator ever reaching a boundary. Rotation has no overlap window by design,
so a request that raced one is retried by the SDK rather than reported as
authority lost.

Two costs, both deliberate. Refresh **rotates**: the secret in the environment is
dead after the first refresh, so a restarted coordinator needs a freshly
provisioned grant rather than the one in its env — inherent to rotation, not new
here, and reported plainly. And a refresh whose response is lost is a lockout, so
it is never retried blind.

### Lost authority is not failed work

| Outcome | `state` | exit | Who to send |
|---|---|---|---|
| every task succeeded | `done` | 0 | nobody |
| a task failed | `done` | 1 | whoever owns the task |
| the coordinator could not act | `lost_authority` | 3 | whoever owns the credential |

A lapsed or refused credential says nothing about the work, so Factory does not
say anything about the work: the mission state becomes `lost_authority` with the
reason in `authority_lost`, unattempted tasks stay **pending** rather than being
marked failed, no further work is submitted, and the notification names the loss.
`credential` records whether refresh was active, how many times it rotated, the
observed lifetime and the margin — so the choice is auditable afterwards.

The distinction is drawn on the contract's own error classes: `401`
(*authentication* — the provider does not accept this credential) is lost
authority; `403` (*authorization* — the credential is live and this action was
refused) is a permissions bug in the mission, and stays a task failure.

## Tests

```bash
cd apps/factory && uv run --extra test pytest -q
```

Runs a deterministic multi-worker mission against the local provider with
Barista Cloud blocked, the same mission against a cloud-shaped provider,
harvest-before-reap, idempotent restart (no duplicate workers/receipts), and
mission budget/grant bounds.

It also runs a mission that spans **more than two grant lifetimes** — with the
provider's lifetime shortened and the clock injected, so nothing sleeps for a
grant — and a mission whose credential lapses, asserting it is reported as lost
authority with every task still pending rather than as work that failed. The
failure-path coverage pins retries, blocked dependents, durable failed receipts,
failed-worker forensics, and successful-worker reap ordering.

Managed-provider acceptance is separate and opt-in:

```bash
cd acceptance
BARISTA_HOST_API_ENDPOINT=... BARISTA_HOST_API_TOKEN=... \
BARISTA_FACTORY_COORDINATOR_IMAGE=... BARISTA_FACTORY_COORDINATOR_DIGEST=... \
BARISTA_FACTORY_WORKER_IMAGE=... BARISTA_FACTORY_WORKER_DIGEST=... \
uv run pytest tests/test_managed_acceptance.py -q
```

The real grant-lifetime case is marked `slow` and excluded from that default
run. Its exact invocation is documented at the top of
`acceptance/tests/test_managed_acceptance.py`.
