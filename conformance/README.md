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
- `grants.delegated` — implemented for **child-session authority** and **grant
  refresh**; see below.
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
| `grants.refresh_preserves_exactly_the_presented_scope` | delegated credentials | the replacement authorizes exactly the presented grant's actions, unchanged across rotations and matching the manifest's declared child authority |
| `grants.refresh_rotates_the_previous_secret` | delegated credentials | the previous secret stops working, and cannot be refreshed either |
| `grants.refresh_cannot_widen_authority` | delegated credentials | a refresh request carrying a scope of its own changes nothing |
| `grants.refresh_refused_after_revocation` | delegated credentials | a grant whose session was deleted is refreshable no longer |
| `grants.refresh_refused_after_expiry` | delegated credentials + a short grant lifetime | a lapsed grant cannot be revived |
| `grants.refresh_refuses_a_credential_with_nothing_to_refresh` | ordinary + delegated credentials | a tenant credential is refused, a session-bound grant is accepted, and an unbound grant is refused when one can be supplied |

Each case asserts **both sides**. A provider that denies everything, or hands
out a dead credential, fails rather than passes: the coordinator's success is
part of the evidence, the foreign session's existence is established with the
ordinary credential before the coordinator is refused on it, and every "refused
after" is measured against an action that demonstrably worked before.

### The suite obtains its own delegated credentials

Before apps-003 it could not, and three of the cases above shipped written and
permanently skipped. Two things changed:

1. A grant is delivered into a session as a `grant://` reference resolved into
   the environment, **under a name the manifest declares** — so a client can
   read it with `exec` and the event stream, using no privilege it did not
   already hold over that session.
2. `POST /v1alpha1/grants/refresh` accepts a live delegated grant and refuses
   anything that is not one. That is the contract's only **positive proof** that
   a client holds delegated authority, so a value read out of an environment can
   be confirmed instead of assumed — and refreshed, so a whole suite run fits
   inside one credential's life.

So the suite installs `contracts/app-manifest/v1alpha1/examples/factory.json`,
creates a `conf-probe-coordinator-…` session, reads and confirms the credential
the provider resolved into it, has that credential create a
`conf-probe-worker-…` child, and reads the narrower grant the provider minted
for it. Those sessions are **sacrificial** — refreshing rotates the secret their
own workloads were given — and the suite deletes them when the run ends. The
report records which route was taken under `environment.delegated_credentials`.

Set `BARISTA_CONFORMANCE_GRANT_ENV` if your provider delivers the credential
under a different variable than the manifest names.

### One refusal a client cannot set up for itself

Refresh must refuse a grant **bound to no session**: the session is what ends a
refresh chain, so an unbound grant would renew past any maximum-lifetime ceiling
in steps that never individually exceed it — and that ceiling exists to force a
re-issue, which is a re-decision. Every credential a black-box client can obtain
arrives inside a session, so it cannot produce one. Supply it if your provider
can mint one:

```bash
BARISTA_CONFORMANCE_UNBOUND_GRANT=…   # a delegated grant bound to no session
```

The assertion is then made inside
`grants.refresh_refuses_a_credential_with_nothing_to_refresh`, whose both-sides
evidence does not depend on it — that case already shows the same endpoint
accepting a session-bound grant and refusing a tenant credential. Absent the
value, the refusal is enforced by the contract text and the provider's own
tests, not by this suite pretending to have watched it.

### Supplying them yourself still works, and still wins

```bash
BARISTA_CONFORMANCE_DELEGATED_APP=factory \
BARISTA_CONFORMANCE_COORDINATOR_TOKEN=… \
BARISTA_CONFORMANCE_COORDINATOR_SESSION=… \
BARISTA_CONFORMANCE_WORKER_TOKEN=… \
BARISTA_CONFORMANCE_WORKER_SESSION=… \
BARISTA_CONFORMANCE_FOREIGN_SESSION=…  # a session the coordinator did not create
uv run barista-conformance --endpoint …
```

Operator-supplied credentials take precedence and are used exactly as given. The
suite does refresh them between cases when the provider allows it, so a run
longer than one grant lifetime does not start reporting expiry as refusal —
which means **your copy stops working**. That is what rotation means.

The app named in `BARISTA_CONFORMANCE_DELEGATED_APP` must declare `session.get`
over its `created_sessions` and withhold it from its children;
`contracts/app-manifest/v1alpha1/examples/factory.json` is exactly such a
manifest and is what the suite installs for the manifest-level cases.

When neither route works, the cases **skip naming the step that failed** — and
since a skip never satisfies an advertised profile, a provider advertising
`grants.delegated` is then reported **not conformant**. That is deliberate: the
suite will not certify delegation it could not watch happen.

### Certifying "an expired grant cannot be refreshed" costs wall-clock time

Revocation is producible through the contract: delete the session the grant is
bound to. **Expiry is not** — it happens by the clock, and no request brings it
forward. So `grants.refresh_refused_after_expiry` reads the lifetime the
provider itself reported in `expires_at` and waits it out when that fits inside
`BARISTA_CONFORMANCE_EXPIRY_WAIT_SECONDS` (default 30). Against a provider whose
grants live fifteen minutes it **skips**, naming the observed lifetime and the
budget, rather than passing on the revocation case's evidence — expiry and
revocation are different requirements, and one does not certify the other.

To certify the profile, run the suite against a tenant configured with a short
delegated-grant lifetime, or raise the budget above that lifetime. This is the
one place the suite asks you to configure the *provider* rather than hand it a
credential.

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
