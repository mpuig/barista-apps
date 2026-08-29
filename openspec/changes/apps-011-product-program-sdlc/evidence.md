# Verification evidence

## Optional GitHub Projects projection phase

- Added a controller-only Projects v2 adapter using a separately configured credential. The adapter resolves one allowlisted issue and project, converges item creation, and writes only the controller-derived Status field.
- Added additive SQLite projection state with desired/projected status, opaque item identity, bounded sanitized error, attempt count, and timestamp. Canonical workflow state commits before projection; API failure cannot turn successful Factory work into failure.
- Startup reconciliation derives every desired status from durable delivery rows. No Project field is read as workflow input or approval authority.
- Added least-scope project setup for missing `Type`, `Program`, `Feature`, `Attempt`, `Dependency`, `Result`, and `PR` fields, plus optional beta provisioning through a separate mode-0600 input file and SSH stdin.
- Project authority is rejected when equal to forge authority and is absent from App Run documents. Public health/config documents expose only non-secret project identity.

## Checks

- GitHub controller/bootstrap suite: 45 passed.
- Ruff check and changed-file formatting passed.
- Supply-chain check passed.
- Strict `apps-011-product-program-sdlc` OpenSpec validation passed.
- PR #49 passed all 15 required CI jobs and merged as `056633996769a6c6e43d35c3352d069e58393d42`.
- GitHub GraphQL introspection confirmed the `createProjectV2`, `createProjectV2Field`, `addProjectV2ItemById`, and `updateProjectV2ItemFieldValue` mutations and their current input/payload shapes.

## Mutation evidence

- Added `projects_v2_item` to the webhook event allowlist. `test_manual_project_webhook_cannot_advance_canonical_workflow` failed because a manual card event entered issue processing instead of remaining inert. The mutation was restored and the test passed.

## Managed deployment and live scope blocker

Reviewed revision `056633996769a6c6e43d35c3352d069e58393d42` was deployed additively to beta. The controller is active and public health exposes the bounded project configuration with `enabled=false`. The source marker equals remote `main`; `/etc/barista` remains mode 0711 and the root-owned controller environment remains mode 0600. Registry-returned Factory, triage, and worker digests remained unchanged from the accepted images.

The active `gh` credential currently has `gist`, `read:org`, `repo`, and `workflow`; GitHub rejects Projects v2 access until `read:project` and `project` are authorized. No live project was created, no project credential was added to production, and no unproven managed profile is advertised. Task 4.5 remains open pending that separately authorized credential.
