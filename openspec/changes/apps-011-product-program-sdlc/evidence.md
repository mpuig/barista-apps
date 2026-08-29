# Verification evidence

## Optional GitHub Projects projection phase

- Added a controller-only Projects v2 adapter using a separately configured credential. The adapter resolves one allowlisted issue and project, converges item creation, and writes only the controller-derived Status field.
- Added additive SQLite projection state with desired/projected status, opaque item identity, bounded sanitized error, attempt count, and timestamp. Canonical workflow state commits before projection; API failure cannot turn successful Factory work into failure.
- Startup reconciliation derives every desired status from durable delivery rows. No Project field is read as workflow input or approval authority.
- Added least-scope project setup for missing `Work Type`, `Program`, `Feature`, `Attempt`, `Dependency`, `Result`, and `PR` fields, plus optional beta provisioning through a separate mode-0600 input file and SSH stdin.
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

## Managed deployment and live project setup

Reviewed revision `056633996769a6c6e43d35c3352d069e58393d42` was deployed additively to beta. The controller remained active with project projection disabled until separate authority became available; `/etc/barista` remained mode 0711 and the root-owned controller environment remained mode 0600. Registry-returned Factory, triage, and worker digests remained unchanged from the accepted images.

A separately created project credential was supplied through `~/.config/barista/github-project-token` at mode 0600. Live setup created and then idempotently reused `https://github.com/users/mpuig/projects/4`. GitHub verified the default `Status` options `Todo`, `In Progress`, and `Done`, plus `Work Type`, `Program`, `Feature`, `Attempt`, `Dependency`, `Result`, and `PR`. No credential value was printed or placed in argv.

The first live call exposed two contract mismatches that offline mocks did not: posting to a path-bearing `httpx` base URL produced `/graphql/`, and GitHub reserves the custom field name `Type`. The adapter now posts to the exact absolute `/graphql` endpoint, endpoint tests assert that URL, and setup uses `Work Type`. The partially created project was explicitly reused rather than duplicated.
