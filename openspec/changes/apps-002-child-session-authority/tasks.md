## 1. Contract (this repo, `contracts/app-manifest/v1alpha1/schema.json`)

- [x] 1.1 Extend `$defs.child_session_limits` with an optional `actions` array of `action_id`, and an optional flag permitting a child to create descendants (default: absent = not permitted). Additive only: a manifest with just `max_concurrent`/`max_total` must stay valid and keep its current meaning.
- [x] 1.2 Add the means to scope a declared action to sessions the app creates. Decide deliberately between a per-action scope and a second block, and record why — a flat list that silently means "own session" is what created this gap. *(Per-action scope; reasoning in design D6.)*
- [x] 1.3 Update `contracts/app-manifest/v1alpha1/examples/factory.json` so the example exercises the new fields rather than documenting the old shape.
- [x] 1.4 The subset rule (a child's actions ⊆ the app's) is a validation requirement, not a schema one — JSON Schema cannot express it. State it in the contract README next to the schema so an implementer does not assume the schema enforces it.

## 2. The factory manifest (this repo)

- [x] 2.1 `apps/factory/manifest.json`: declare what its workers receive (narrower than the coordinator's own set), and that workers may not create descendants — the two halves its own ratified scenario already assumes.
- [x] 2.2 Add a `grant://` secret reference so the coordinator receives a delegated credential rather than expecting a tenant key. It currently declares only `secret://factory/notify-token`.
- [x] 2.3 Validate the manifest against the amended schema in CI, so the example and the app cannot drift from the contract.

## 3. Conformance (this repo)

- [x] 3.1 Turn `factory-app`'s ratified scenario into a provider conformance test: a worker without child-create permission calls session create and is denied, while the coordinator's own create succeeds. It has been an unimplementable sentence until now. *(`grants.worker_cannot_create_descendants`. Runs on operator-supplied delegated credentials; honest skip otherwise — see design D7.)*
- [x] 3.2 A conformance test that a child receives *only* the declared subset — an action the coordinator holds and the child was not given must be refused to the child. *(`grants.child_receives_only_declared_subset`.)*
- [x] 3.3 A conformance test that an app's authority stops at its own children: the same action against a session it did not create is refused. *(`grants.authority_stops_at_own_children`.)*

Also added, because the delta spec's install-time scenarios needed somewhere to
be observable and they need no delegated credential:
`grants.child_authority_manifest_accepted` and
`grants.over_delegating_manifest_refused` (the only place the subset rule of
task 1.4 is enforceable, since the schema cannot carry it).

## 4. Provider work (barista-cloud, tracked here for sequencing only)

- [ ] 4.1 Mint the coordinator's grant over a selector covering its children. Prefix selectors already exist and are tested (`session:<prefix>-*`); what is missing is a naming convention that makes a child's name derivable from its parent's.
- [ ] 4.2 Mint each child's narrower grant at child-session create, from the same manifest. The provider is the only minter (design D2) — no `grant.*` action is introduced.
- [ ] 4.3 Enforce the descendant rule at session create for a caller whose authority came from a child grant.
- [ ] 4.4 Reject at install a manifest whose child actions exceed the app's own, naming the offending actions (design D3).

Note for 4.1: `barista_app_factory.coordinator` names the coordinator session
`<mission>-coordinator` and each worker `<mission>-<task>`, so a worker's name is
derived from the *mission*, not from its parent's name. A
`session:<coordinator-name>-*` prefix selector would match none of them. The
naming convention has to be chosen with that in mind (or the coordinator's naming
changed to make workers a prefix of it).

Note for 4.4: `contracts/app-manifest/v1alpha1/rules.py` is the stdlib-only
reference implementation, and
`contracts/app-manifest/v1alpha1/semantically-invalid/` holds fixtures that pass
the schema and must be refused.

## 5. Not in this change, and blocking in practice

- [ ] 5.1 **Grant lifetime.** 15-minute TTL, write-once env var, 3600-second missions (design D4). Pick one of the three options recorded there. Until then an app authenticates for a quarter of an hour of an hour-long mission, and "the factory is an app" cannot be claimed on scoping alone.
- [ ] 5.2 Decide whether a provider-specific worker primitive is ever exposed through this contract. Design D5 says no for now, and says why the name collision is not a reason to.
- [ ] 5.3 **No way to obtain a delegated grant through the Host API** (design D7). Three of the §3 conformance cases run on operator-supplied credentials because `v1alpha1` exposes no endpoint that hands a client the grant a provider minted. Either add one, or accept operator-supplied probes as the permanent answer — a contract decision, deliberately not made here.
