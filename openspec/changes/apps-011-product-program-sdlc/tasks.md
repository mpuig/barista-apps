## 1. Closed protocols

- [x] 1.1 Define bounded canonical BRD decision and artifact schemas.
- [x] 1.2 Define bounded feature-plan and acyclic dependency schemas.
- [x] 1.3 Define approval correlation and final program-result schemas.
- [x] 1.4 Add parser, canonicalization, hostile-input, and mutation tests.

## 2. Factory operations and workers

- [x] 2.1 Add typed `product-brief` operation with bounded clarification.
- [x] 2.2 Add typed `feature-plan` operation with independent plan validation.
- [x] 2.3 Add typed `program-acceptance` operation without forge, model, or inherited Host API authority.
- [x] 2.4 Add deterministic BRD-author, planner/architect, feature-worker, and independent acceptance reference stages.
- [x] 2.5 Preserve sessions and prevent delivery on integrity failure.

## 3. Durable controller state machine

- [x] 3.1 Add additive SQLite program, BRD-attempt, approval, feature, dependency, and receipt tables.
- [x] 3.2 Accept only correlated authorized merged-BRD events as approval.
- [x] 3.3 Independently verify and idempotently create bounded feature issues.
- [x] 3.4 Release dependency-ready features from current `main` and ingest verified draft PR outcomes.
- [x] 3.5 Run final acceptance only after all required feature merges.
- [x] 3.6 Keep duplicate, stale, self, marker, unauthorized, oversized, and unrelated events inert.

## 4. Optional GitHub Projects projection

- [x] 4.1 Add separately credentialed, controller-only Projects v2 adapter.
- [x] 4.2 Project canonical issue status without making project fields authoritative.
- [x] 4.3 Persist projection success/failure and keep workflow progress independent of projection availability.
- [x] 4.4 Reconcile project fields from durable controller state after restart.
- [x] 4.5 Provision and validate the disposable presentation project with separate project authority.

## 5. Presentation application and acceptance

- [x] 5.1 Seed one-container backend-served frontend plus SQLite presentation application.
- [x] 5.2 Add offline incomplete brief → clarification → BRD PR → merge → feature plan/issues → gated PRs → final acceptance.
- [x] 5.3 Add security mutations for approval, dependencies, publication, project authority, and credential isolation.
- [x] 5.4 Run Apps suites, supply-chain checks, strict OpenSpec, and current-head CI.
- [x] 5.5 Deploy digest-pinned Apps images and perform disposable managed GitHub acceptance.
- [x] 5.6 Record evidence and visual walkthrough artifacts.
