## 1. Contract (this repo)

- [x] 1.1 Add the refresh path to `contracts/host-api/v1alpha1/openapi.yaml`. Request body carries **no scope** — if it accepts a resource or action list, it is issuance, not refresh (design D1). Response returns the replacement secret plus the actions and expiry the caller can now rely on.
- [x] 1.2 Document the operation as available only when the provider advertises `grants.delegated`, and reachable only with a delegated grant as the credential — a tenant credential has no grant to refresh.
- [x] 1.3 State in the contract README that refresh **rotates**: the previous secret stops working, and a caller that loses the response has locked itself out (design D2). That failure is intended and needs to be discoverable without reading the spec deltas.
- [x] 1.4 *(added in implementation)* Two refusals the original list left out, both now contract text with their reasoning and pinned by golden tests: a grant with **no session binding** cannot be refreshed (nothing would bound the chain), and `deleteSession` **SHALL revoke the grants bound to that session** — the premise design D5 relied on, which the reference provider did not actually satisfy. Plus: a refusal SHALL leave the presented credential working, and `grants.delegated` is the whole gate (no second capability id, no vendor extension).

## 2. Conformance (this repo) — the practical payoff

- [x] 2.1 Un-skip the three delegated-authority tests `apps-002` left pending on an operator-supplied credential: they can now obtain one the way an app does. If any still cannot, say which and why rather than leaving a vacuous pass. **All three run unattended.** The suite installs the contract's child-authority example, reads the grant the provider resolved into a probe session (the env var the manifest declares, via exec + events), confirms it by refreshing it — refresh being the contract's only positive proof that a client holds a live delegated grant — and lets the provider mint a child beneath it. Operator-supplied credentials still take precedence.
- [x] 2.2 A conformance test that the replacement authorizes exactly the presented grant's actions — no more, no fewer. `grants.refresh_preserves_exactly_the_presented_scope`, measured against the manifest's declared child authority as an independent source of truth, held across two rotations, and both sides behaviourally.
- [x] 2.3 A conformance test that the previous secret is refused after a refresh. `grants.refresh_rotates_the_previous_secret` — and the replaced secret cannot be refreshed either.
- [x] 2.4 A conformance test that an expired grant and a revoked grant are both refused refresh (design D3). Assert **both** sides for each, so a provider that refuses everything fails instead of passing. Split, because the two are not equally observable: `grants.refresh_refused_after_revocation` always runs (deleting the grant's session is the only revocation a client can perform); `grants.refresh_refused_after_expiry` reads the lifetime the provider reported and waits it out inside a budget, and otherwise **skips** naming the observed lifetime — expiry happens by the clock, and passing it on the revocation case's evidence would certify a different requirement.
- [x] 2.5 The reference double implements refresh, and its dishonest modes are tested: a double that keeps the old secret working, and one that reads scope from the request, must both be caught. Both, plus a third the provider work surfaced (refreshing a grant with no session binding) and two honest failure modes (a provider that advertises the profile without offering refresh; one that delivers grants where a client cannot read them).

## 3. Factory (this repo)

- [x] 3.1 The coordinator refreshes before its credential lapses. Pick the margin deliberately and record it — refreshing at the last moment against a provider whose clock differs slightly is a lockout. **20% of the observed lifetime, floored at 60s, capped at half the lifetime** — 180s for a 900s grant. Reasoning recorded in `credential.refresh_margin_seconds`' docstring and `apps/factory/README.md`, and asserted in a test so it cannot drift. Checked at each task boundary *and* by a ticker, because a task with an hour's timeout never reaches a boundary.
- [x] 3.2 A lapsed credential is reported as lost authority, not as failed work. The distinction matters to whoever reads the mission result: one is an operator problem, the other is a task problem. Mission state `lost_authority` + `authority_lost` reason, unattempted tasks left **pending**, no further work submitted, exit code 3 (distinct from 1). Drawn on the contract's own classes: 401 authentication is lost authority; 403 authorization stays a task failure.
- [x] 3.3 A test that a mission spanning more than one grant lifetime completes, with the provider's lifetime shortened rather than the test lengthened. The double's `grant_lifetime_seconds` is shortened and its clock injected, so a mission crossing >2 lifetimes runs without sleeping for one.

## 4. Provider work (barista-cloud, tracked here for sequencing)

- [ ] 4.1 Implement the endpoint: authenticate the presented grant, refuse it unless live, mint a replacement copying `resource`/`actions`/`session_name`/`execution_epoch` from the row, and stop accepting the old secret.
- [ ] 4.2 Rotation must be atomic with respect to authorization — there must be no instant where both secrets work, and none where neither does.
- [ ] 4.3 Advertise the capability in discovery so an app can find out before depending on it.
- [ ] 4.4 Confirm the existing "a grant cannot mint or revoke a grant" test still passes unchanged. If refresh required weakening it, the implementation has drifted into issuance.

## 5. Not in this change

- [ ] 5.1 A maximum total lifetime for a refresh chain. The bound is the session (design D5); a number here would be arbitrary and portable contracts should not carry arbitrary numbers.
- [ ] 5.2 Any operation that creates a grant from outside the provider's minting path. Still `grant.issue`, still unnecessary, still refused.
