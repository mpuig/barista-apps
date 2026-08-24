# apps/factory

A durable **software-factory** coordinator as a portable Barista app: it fans a
mission across worker sessions, supervises them, harvests each successful
worker's receipt and artifacts **before** reaping it, keeps failed workers for
forensics, and reconstructs its state idempotently after a restart.

Ported from `barista-cloud`'s `demos/factory` to run through **only** the Host
API/SDK — no privileged node contract, no provider database/bucket, no
Cloud-specific shape.

- **Manifest:** [`manifest.json`](manifest.json)
- **Mission schema:** [`mission.schema.json`](mission.schema.json)
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

## How it behaves

- **One durable coordinator owns mission state** — task graph, worker handles,
  attempts, and receipts persist after every transition. A restart reconstructs
  the mission and re-ensures workers with a stable idempotency key rather than
  duplicating an accepted task.
- **Harvest before reap** — for every successful worker, Factory registers the
  result receipt (and declared artifacts) on the durable coordinator session
  **before** deleting the worker. A retrievable receipt proves harvest completed
  before the reap.
- **Failed workers survive** — they are left in place for bounded forensics
  instead of being deleted.
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
authority with every task still pending rather than as work that failed.
