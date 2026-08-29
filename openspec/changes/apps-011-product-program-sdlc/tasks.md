## 1. Closed protocols

- [ ] 1.1 Define bounded canonical BRD decision and artifact schemas.
- [ ] 1.2 Define bounded feature-plan and acyclic dependency schemas.
- [ ] 1.3 Define approval correlation and final program-result schemas.
- [ ] 1.4 Add parser, canonicalization, hostile-input, and mutation tests.

## 2. Factory operations and workers

- [ ] 2.1 Add typed `product-brief` operation with bounded clarification.
- [ ] 2.2 Add typed `feature-plan` operation with independent plan validation.
- [ ] 2.3 Add typed `program-acceptance` operation without forge, model, or Host API authority.
- [ ] 2.4 Add deterministic BRD-author, planner, architect/reviewer reference workers.
- [ ] 2.5 Preserve sessions and prevent delivery on integrity failure.

## 3. Durable controller state machine

- [ ] 3.1 Add additive SQLite program, BRD-attempt, approval, feature, dependency, and receipt tables.
- [ ] 3.2 Accept only correlated authorized merged-BRD events as approval.
- [ ] 3.3 Independently verify and idempotently create bounded feature issues.
- [ ] 3.4 Release dependency-ready features from current `main` and ingest verified draft PR outcomes.
- [ ] 3.5 Run final acceptance only after all required feature merges.
- [ ] 3.6 Keep duplicate, stale, self, marker, unauthorized, oversized, and unrelated events inert.

## 4. Optional GitHub Projects projection

- [x] 4.1 Add separately credentialed, controller-only Projects v2 adapter.
- [x] 4.2 Project canonical issue status without making project fields authoritative.
- [x] 4.3 Persist projection success/failure and keep workflow progress independent of projection availability.
- [x] 4.4 Reconcile project fields from durable controller state after restart.
- [ ] 4.5 Provision and validate the disposable presentation project with least project scopes.

## 5. Presentation application and acceptance

- [ ] 5.1 Seed one-container backend-served frontend plus SQLite presentation application.
- [ ] 5.2 Add offline incomplete brief → clarification → BRD PR → merge → feature plan/issues → gated PRs → final acceptance.
- [ ] 5.3 Add security mutations for approval, dependencies, publication, project authority, and credential isolation.
- [ ] 5.4 Run Apps suites, supply-chain checks, strict OpenSpec, and current-head CI.
- [ ] 5.5 Deploy digest-pinned Apps images and perform disposable managed GitHub acceptance.
- [ ] 5.6 Record evidence and visual walkthrough artifacts.
