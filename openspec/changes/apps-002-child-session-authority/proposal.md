## Why

The ratified `factory-app` spec already requires two-level delegation:

> Workers SHALL receive narrower delegated grants and SHALL not inherit the
> coordinator's full authority.
>
> **WHEN** a worker without child-create permission calls session create
> **THEN** the provider denies it even though the coordinator may create workers

The App Manifest cannot express either half of that. `permissions.actions` is a
single flat list with no notion of *level*, and `permissions.child_sessions` —
which is where the contract already acknowledges that some apps spawn children —
carries only `max_concurrent` and `max_total`. It bounds *how many* children an
app may create and says nothing about what the app may do to them, or what they
receive.

That gap is not theoretical. A provider that mints a delegated grant from a
manifest can only scope it to the app's own session, because that is the only
resource the manifest names. So the ratified factory app — whose entire job is
fanning work out across sessions it creates — receives a grant that cannot touch
a single one of them. Measured against a real provider on 2026-08-24: the
coordinator's declared `session.get`/`session.delete`/`session.exec`/
`session.events` all map to grant actions successfully, and every one lands on a
selector (`session:<its own session>`) that excludes the children the calls are
for. `session.create` and `session.list` are withheld entirely, because the
provider's collection routes authorize against a resource a session-scoped
selector never matches.

So an app cannot today be what `factory-app` says Factory is: an ordinary
portable app that coordinates other sessions.

## What Changes

- **`permissions.child_sessions` gains authority, not just counts.** An app
  declares what a child session receives — a set of actions that MUST be a subset
  of the app's own — and whether a child may itself create children. The default
  is that it may not, which is exactly the ratified scenario above.
- **The manifest gains a way to say "these actions, on the sessions I create."**
  Without it a provider has no basis for a selector wider than the app's own
  session, and the coordinator's declared actions remain unusable.
- **The provider remains the only minter.** A coordinator never mints a grant for
  its children; it asks for a child session and the *provider* mints that child's
  narrower grant from the same manifest. This is deliberate: `grant.*` actions do
  not exist, and a grant that could mint another grant would be a second key
  rather than scoped authority. Two-level delegation is achieved without weakening
  that rule.

## Capabilities

### New Capabilities
<!-- None. This gives an existing permission block the dimension it is missing. -->

### Modified Capabilities
- `app-manifest`: `child_sessions` expresses delegated authority and descendant
  policy, and declared actions can be scoped to created sessions.
- `factory-app`: its already-ratified delegation requirement becomes expressible,
  and therefore testable, in a manifest.

## Impact

- **Contract**: additive to `contracts/app-manifest/v1alpha1/schema.json` — new
  optional properties inside `child_sessions`. A manifest written before this
  change stays valid and keeps its current meaning (no child authority), so the
  change is backward compatible on the wire and in behaviour.
- **`apps/factory/manifest.json`**: declares what its workers receive. It
  currently declares only counts, which is why its own ratified scenario has no
  implementation behind it.
- **Providers**: a conformant provider mints the coordinator's grant over a
  selector that covers its children, and mints each child's narrower grant at
  child-session create. In `barista-cloud` the mechanism already exists — prefix
  selectors (`session:<prefix>-*`) are implemented and tested, and §3.3 already
  mints from a manifest at session create; what is missing is the manifest input
  and the child naming convention that makes a prefix derivable.
- **Conformance**: the `factory-app` scenario ("a worker without child-create
  permission calls session create → denied") becomes a provider conformance test
  rather than an unimplementable sentence.
- **Explicitly out of scope**: `worker.invoke`/`worker.read`/`worker.write`. Those
  belong to a provider-specific worker primitive, not to this contract — the
  ratified factory app coordinates *worker sessions* through the portable Host
  API, and needs no new action vocabulary to do it. Conflating the two names cost
  an afternoon of analysis and is worth stating here so it is not re-proposed.
- **Also out of scope, and blocking in practice**: grant lifetime. A delegated
  grant lives 15 minutes and arrives in a write-once environment variable, while
  a factory mission's default task timeout is 3600 seconds. Renewal is a separate
  decision (see design D4) and no amount of scoping fixes it.
