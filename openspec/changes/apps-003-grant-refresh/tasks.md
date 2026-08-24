## 1. Contract (this repo)

- [ ] 1.1 Add the refresh path to `contracts/host-api/v1alpha1/openapi.yaml`. Request body carries **no scope** — if it accepts a resource or action list, it is issuance, not refresh (design D1). Response returns the replacement secret plus the actions and expiry the caller can now rely on.
- [ ] 1.2 Document the operation as available only when the provider advertises `grants.delegated`, and reachable only with a delegated grant as the credential — a tenant credential has no grant to refresh.
- [ ] 1.3 State in the contract README that refresh **rotates**: the previous secret stops working, and a caller that loses the response has locked itself out (design D2). That failure is intended and needs to be discoverable without reading the spec deltas.

## 2. Conformance (this repo) — the practical payoff

- [ ] 2.1 Un-skip the three delegated-authority tests `apps-002` left pending on an operator-supplied credential: they can now obtain one the way an app does. If any still cannot, say which and why rather than leaving a vacuous pass.
- [ ] 2.2 A conformance test that the replacement authorizes exactly the presented grant's actions — no more, no fewer.
- [ ] 2.3 A conformance test that the previous secret is refused after a refresh.
- [ ] 2.4 A conformance test that an expired grant and a revoked grant are both refused refresh (design D3). Assert **both** sides for each, so a provider that refuses everything fails instead of passing.
- [ ] 2.5 The reference double implements refresh, and its dishonest modes are tested: a double that keeps the old secret working, and one that reads scope from the request, must both be caught.

## 3. Factory (this repo)

- [ ] 3.1 The coordinator refreshes before its credential lapses. Pick the margin deliberately and record it — refreshing at the last moment against a provider whose clock differs slightly is a lockout.
- [ ] 3.2 A lapsed credential is reported as lost authority, not as failed work. The distinction matters to whoever reads the mission result: one is an operator problem, the other is a task problem.
- [ ] 3.3 A test that a mission spanning more than one grant lifetime completes, with the provider's lifetime shortened rather than the test lengthened.

## 4. Provider work (barista-cloud, tracked here for sequencing)

- [ ] 4.1 Implement the endpoint: authenticate the presented grant, refuse it unless live, mint a replacement copying `resource`/`actions`/`session_name`/`execution_epoch` from the row, and stop accepting the old secret.
- [ ] 4.2 Rotation must be atomic with respect to authorization — there must be no instant where both secrets work, and none where neither does.
- [ ] 4.3 Advertise the capability in discovery so an app can find out before depending on it.
- [ ] 4.4 Confirm the existing "a grant cannot mint or revoke a grant" test still passes unchanged. If refresh required weakening it, the implementation has drifted into issuance.

## 5. Not in this change

- [ ] 5.1 A maximum total lifetime for a refresh chain. The bound is the session (design D5); a number here would be arbitrary and portable contracts should not carry arbitrary numbers.
- [ ] 5.2 Any operation that creates a grant from outside the provider's minting path. Still `grant.issue`, still unnecessary, still refused.
