# conformance

Black-box conformance suite for **Barista Host API** providers. It proves a
provider preserves portable app semantics and advertises only behavior it
actually supports — through the published contract alone, with no private hooks.

- **OpenSpec:** `openspec/changes/apps-001-portable-agent-apps/specs/provider-conformance/spec.md`

## Run it against a provider

```bash
cd conformance
uv run barista-conformance \
  --endpoint http://localhost:8088 \
  --provider-name local --provider-version 0.1.0 \
  --report report.json
```

Or from the environment:

```bash
BARISTA_HOST_API_ENDPOINT=http://localhost:8088 \
BARISTA_CONFORMANCE_STANDALONE=1 \
uv run barista-conformance
```

The process exits non-zero when the provider is **not** conformant, so CI can
gate on it. The JSON report records contract version, suite version, provider
identity, advertised profiles, per-case status, and violations.

## The rules it enforces

- **Core profile must fully pass** — discovery, manifest rejection,
  ensure/get/idempotency, exec + readable operations, resumable event cursors,
  content-addressed artifacts, classified errors, no provider-internal leakage,
  cleanup, and *unsupported profiles return a capability error rather than a
  fake success*.
- **A skip never certifies an advertised profile.** If a provider advertises an
  optional profile, that profile must have at least one case and all its cases
  must pass. Advertising a profile this suite cannot demonstrate is a violation.
- **Standalone (Cloud-absent) is enforced, not documented.** With
  `--standalone`, a process-wide audit-hook guard fails the run if anything
  resolves Barista Cloud DNS, connects to a Cloud address, or imports a
  proprietary module.

## Profiles

- `core` — mandatory.
- `session.pause_resume` — implemented.
- `grants.delegated` — implemented for **child-session authority**; see below.
- `session.snapshot.exact`, `session.fork`, `capsule.export`, `capsule.import`,
  `story.publish`, `branch.evaluation` — registered profile identifiers; deep
  cases land as the corresponding Host API endpoints are defined (they depend on
  the kernel fork/capsule work and later apps milestones). Until then this suite
  version honestly **cannot certify** a provider that advertises them.

## `grants.delegated`: child-session authority

These implement the ratified `factory-app` requirement that "workers SHALL
receive narrower delegated grants and SHALL not inherit the coordinator's full
authority", which until now had no test behind it.

| Case | Needs | Proves |
|---|---|---|
| `grants.child_authority_manifest_accepted` | ordinary credential | a manifest declaring what its children receive installs |
| `grants.over_delegating_manifest_refused` | ordinary credential | a child action set exceeding the app's own is refused **at install**, naming the offending action |
| `grants.worker_cannot_create_descendants` | delegated credentials | a worker's session create is denied *while the coordinator's succeeds* |
| `grants.child_receives_only_declared_subset` | delegated credentials | an action the coordinator holds and the child was not given is refused to the child |
| `grants.authority_stops_at_own_children` | delegated credentials | a `created_sessions` scope does not reach a session the app did not create |

Each case asserts **both sides**. A provider that denies everything, or hands
out a dead credential, fails rather than passes: the coordinator's success is
part of the evidence, and the foreign session's existence is established with
the ordinary credential before the coordinator is refused on it.

### Why three of them need credentials you must supply

**Host API `v1alpha1` has no endpoint that hands a delegated grant to a
client.** The provider mints a child's grant and delivers it *into* the child
session — a `grant://` reference resolved into its environment. A black-box
suite runs outside every session, so it cannot obtain one through the published
contract, and it will not reach around the contract to get one.

So the operator supplies them: install an app that declares child authority, let
the provider create a coordinator and a worker from it, and pass the two
credentials it minted:

```bash
BARISTA_CONFORMANCE_DELEGATED_APP=factory \
BARISTA_CONFORMANCE_COORDINATOR_TOKEN=… \
BARISTA_CONFORMANCE_COORDINATOR_SESSION=… \
BARISTA_CONFORMANCE_WORKER_TOKEN=… \
BARISTA_CONFORMANCE_WORKER_SESSION=… \
BARISTA_CONFORMANCE_FOREIGN_SESSION=…  # a session the coordinator did not create
uv run barista-conformance --endpoint …
```

Without them the three cases **skip with that reason** — and since a skip never
satisfies an advertised profile, a provider advertising `grants.delegated`
without supplying them is reported **not conformant**. That is deliberate: the
suite will not certify delegation it could not watch happen. Either the operator
provides the credentials, or a future contract change adds a way to obtain a
delegated grant through the API.

The app named in `BARISTA_CONFORMANCE_DELEGATED_APP` must declare
`session.get` over its `created_sessions` and withhold it from its children;
`contracts/app-manifest/v1alpha1/examples/factory.json` is exactly such a
manifest and is what the suite installs for the two manifest-level cases.

### The subset rule is not in the schema

A child's actions must be a subset of the app's own, and **JSON Schema cannot
express that** — `contracts/app-manifest/v1alpha1/schema.json` accepts a
manifest that over-delegates. `grants.over_delegating_manifest_refused` is
therefore the only place the rule becomes observable, and it guards its own
fixture: the fixture must *pass* the schema, or the case would merely be
re-testing malformed-manifest rejection. The suite reuses the contract's
reference implementation (`v1alpha1/rules.py`) rather than keeping a second copy
that could disagree with it.

## Self-tests

The suite ships an in-process mock provider (`tests/mock_provider.py`) — a test
double, *not* the real local provider — so its own logic is verified offline:

```bash
cd conformance && uv run --extra test pytest -q
```
