## Why

A delegated grant is minted by the provider and injected into a session as an
environment variable. That works exactly once. The variable cannot be rewritten
in a running process, and the credential expires — in the reference provider,
after 15 minutes. A Factory mission's default task timeout is 3600 seconds.

So an app authenticates for a quarter of an hour of an hour-long mission and then
holds a dead credential, with no way to obtain a live one. `apps-002` made
delegated authority *expressible*; this makes it *survivable*.

The same absence has a second cost. Because a grant can only be delivered into a
session, a conformance suite standing outside every session can never hold one —
so `apps-002` shipped three delegated-authority tests that are fully written and
permanently skipped, waiting on an operator to supply a credential by hand. A
property that can only be tested by hand is one that regresses quietly.

## What Changes

- **A session can refresh its own delegated grant.** A new Host API endpoint,
  authenticated by the grant the caller already holds, returns a fresh secret
  carrying **the same resource and the same actions** with a new expiry. The
  previous secret stops working.
- **Refresh cannot widen.** The new grant's scope is copied from the row, not from
  the request. There is no parameter that could broaden it, which is what keeps
  this from becoming the `grant.issue` action the contract deliberately does not
  have.
- **Refresh requires a live grant.** An expired or revoked grant cannot be
  refreshed. Letting it lapse is a lockout, not a soft failure — which is what
  makes revocation still mean something.
- **Gated on the existing `grants.delegated` capability.** A provider that does
  not offer delegated grants does not gain an endpoint, and an app that needs
  refresh discovers that before it depends on it.

## Capabilities

### New Capabilities
<!-- None. This gives an existing optional capability the operation it was
     missing; `grants.delegated` already exists and Factory already declares it. -->

### Modified Capabilities
- `host-api`: gains a grant-refresh operation, available when the provider
  advertises `grants.delegated`.
- `factory-app`: a mission may outlive a single grant lifetime, which its own
  default timeout already assumes.

## Impact

- **Contract**: one path added to `contracts/host-api/v1alpha1/openapi.yaml`.
  Additive — a provider without `grants.delegated` is unaffected, and no existing
  operation changes shape.
- **Conformance**: the three delegated-authority tests `apps-002` left pending on
  an operator-supplied credential can obtain one the way an app does, and run
  unattended. This is the larger practical win: it converts a hand-tested
  property into a gate.
- **Providers**: implement one endpoint. The reference provider already stores
  everything it needs — resource, actions, expiry, revocation and the
  session/epoch binding are all on the grant row.
- **Security posture, stated plainly**: this is deliberately *not* a way to obtain
  authority, only to keep authority already granted. The distinction is that the
  scope comes from the stored row rather than the request. It remains true that a
  grant can neither mint nor revoke — including itself; refresh replaces a
  credential without changing what it may do.
- **Out of scope**: any operation that *creates* a grant from outside the
  provider's own minting path. That would be `grant.issue`, it is not needed to
  solve either problem here, and the argument against it is unchanged.
