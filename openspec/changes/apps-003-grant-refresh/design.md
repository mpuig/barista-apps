# Design — refreshing a delegated grant

## D1. Refresh is not issuance, and the difference is where the scope comes from

The contract has no `grant.issue` action on purpose: a credential that can mint
credentials is a key that makes keys, and "narrower" is not a property anything
checks at the moment of use. That argument is sound and this change does not
touch it.

Refresh is a different operation, and the distinction is mechanical rather than
rhetorical: **the new grant's resource and actions are copied from the stored
row.** The request carries no scope, so there is no input a caller could use to
widen. An implementation that reads *any* scope from the request body has
implemented issuance and should be rejected in review.

That is also why refresh needs no new action in the manifest vocabulary. Holding
a grant is the authority to keep holding it.

## D2. Rotation, not extension

Extending `expires_at` on the existing row would be simpler and is the wrong
trade. The secret is what leaks; a leaked secret with a repeatedly-extended
expiry is a permanent credential. Rotating means the previous secret stops
working, so the window a leaked one is worth stays bounded by the refresh
interval rather than by the session's lifetime.

The cost is that a caller which refreshes and then loses the response has locked
itself out. That is the right failure: it is loud, it is immediate, and the
alternative is a credential that outlives its holder's knowledge of it.

## D3. A lapsed grant cannot be refreshed

Refresh requires a grant that is live — not expired, not revoked. This is the
line that keeps revocation meaningful: if an expired grant could be refreshed,
expiry would be advisory, and if a revoked one could be, revocation would be a
suggestion.

It follows that an app must refresh *before* it needs to, and that a long pause
is indistinguishable from a lapse. Both are true and both are intended.

## D4. Why this also fixes conformance, which was the hidden cost

`apps-002` could not test delegated authority without an operator supplying a
credential by hand, because there was no way for a client to obtain one. Three
tests shipped written-and-skipped.

With refresh, a probe session obtains its credential exactly as an app does, so
those tests run unattended. This is worth naming as a first-order benefit rather
than a side effect: a security property that can only be verified manually is one
that regresses between the times someone remembers to check.

## D5. What bounds a refresh chain

Nothing in this change caps how many times a grant may be refreshed, and that is
deliberate — the bound is the session. A delegated grant is bound to a session and
an execution epoch; when the session ends the grant has nothing to act on, and a
provider that revokes on session deletion (as the reference one does) ends the
chain there.

Adding a maximum total lifetime would be a second, independent policy. It is not
needed to solve either problem here, and inventing a number now would put an
arbitrary ceiling into a portable contract. Recorded so its absence reads as a
decision rather than an oversight.
