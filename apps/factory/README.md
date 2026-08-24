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

## Tests

```bash
cd apps/factory && uv run --extra test pytest -q
```

Runs a deterministic multi-worker mission against the local provider with
Barista Cloud blocked, the same mission against a cloud-shaped provider,
harvest-before-reap, idempotent restart (no duplicate workers/receipts), and
mission budget/grant bounds.
