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
- The live-contract correction in PR #51 passed all 15 required CI jobs and merged as `abf84bddab6d485be4786d526c490c95966cba4a`.
- GitHub GraphQL introspection confirmed the `createProjectV2`, `createProjectV2Field`, `addProjectV2ItemById`, and `updateProjectV2ItemFieldValue` mutations and their current input/payload shapes.

## Mutation evidence

- Added `projects_v2_item` to the webhook event allowlist. `test_manual_project_webhook_cannot_advance_canonical_workflow` failed because a manual card event entered issue processing instead of remaining inert. The mutation was restored and the test passed.

## Managed deployment and live project setup

Reviewed revision `056633996769a6c6e43d35c3352d069e58393d42` was deployed additively to beta. The controller remained active with project projection disabled until separate authority became available; `/etc/barista` remained mode 0711 and the root-owned controller environment remained mode 0600. Registry-returned Factory, triage, and worker digests remained unchanged from the accepted images.

A separately created project credential was supplied through `~/.config/barista/github-project-token` at mode 0600. Live setup created and then idempotently reused `https://github.com/users/mpuig/projects/4`. Explicit `--public` publication returned `public=true`, and an unauthenticated request returned HTTP 200. GitHub verified the default `Status` options `Todo`, `In Progress`, and `Done`, plus `Work Type`, `Program`, `Feature`, `Attempt`, `Dependency`, `Result`, and `PR`. No credential value was printed or placed in argv.

The first live call exposed two contract mismatches that offline mocks did not: posting to a path-bearing `httpx` base URL produced `/graphql/`, and GitHub reserves the custom field name `Type`. The adapter now posts to the exact absolute `/graphql` endpoint, endpoint tests assert that URL, and setup uses `Work Type`. The partially created project was explicitly reused rather than duplicated.

Corrected revision `abf84bddab6d485be4786d526c490c95966cba4a` was deployed from clean remote `main`. The controller environment was reprovisioned over SSH stdin with separate runtime-forge and project files, all checked at mode 0600 without printing their contents. Public health reports Project #4 enabled; the source marker matches the deployed revision, `/etc/barista` remains mode 0711, and `/etc/barista/github-factory-demo.env` remains root-owned mode 0600.

Startup reconciliation added historical issues #4 and #5 to Project #4 and converged their terminal Status to `Done`. The controller recorded matching desired/projected states, opaque item IDs, no error, and one attempt. A live authority mutation then moved issue #5 manually to `In Progress`; its canonical controller state remained `succeeded`. Restart reconciliation restored the Project field to `Done`, and the durable projection attempt count advanced to two. This proves the board is a corrective read model rather than workflow authority.

## Product-program runtime implementation phase

- Added Factory `product-brief`, `feature-plan`, and `program-acceptance` operations. Planning verifies the exact approved commit and BRD digest before launching a separately installed planner; final acceptance uses the stripped local-check environment and retains failed or integrity-invalid sessions.
- Added one digest-shared product worker image with separate `github-brd-author`, `github-feature-planner`, and `github-feature-worker` install identities. Its deterministic reference product is a one-runtime-container Python service with bounded APIs, SQLite under `/data`, backend-served compiled frontend assets, responsive source assets, a multi-stage Dockerfile, and offline persistence tests.
- Added additive durable program/feature/dependency/approval state, idempotent marker-bound feature issue publication, exact authorized BRD/feature merge correlation, serial dependency release from current `main`, restart recovery, and final assembled-commit acceptance.
- Feature-issue webhook events are explicitly inert so GitHub issue creation cannot bypass controller dependency gates. Fresh duplicate merge deliveries converge without failing or advancing canonical state twice.
- Project projection now durably carries controller-owned `Work Type`, `Program`, `Feature`, `Attempt`, `Dependency`, `Result`, and `PR` presentation fields. Startup still writes desired state outward and never reads Project state as input.

### Offline checks

- Factory suite: 84 passed.
- GitHub controller/bootstrap suite: 55 passed.
- Product worker suite: 5 passed.
- Contract suite: 74 passed.
- Conformance self-tests: 22 passed.
- Local provider suite: 14 passed.
- Python SDK suite: 68 passed.
- Standalone acceptance: 1 passed, 3 skipped, 1 deselected.
- Supply-chain check passed.
- Every active OpenSpec change passed strict validation.
- Changed-scope Ruff checks and `git diff --check` passed. Repository-wide Ruff still reports unrelated pre-existing findings in untouched legacy packages and is not a configured CI gate.
- Local OCI builds were attempted but Docker Desktop was unavailable; managed-node digest builds and runtime acceptance remain required before this phase is marked complete.
- Apps PR #54 ran all 15 required current-head CI jobs successfully for implementation commit `6d93da4cde527976f1862f59421768379198e537`.

### Product-program mutation evidence

- Disabled the reserved feature-issue ingress guard. The full program test failed because the controller accepted a feature issue as an ordinary independent workflow. The mutation was restored.
- Disabled the authorized-merger predicate. The merge-authority test failed because an unrelated actor could advance the correlated BRD PR. The mutation was restored.
- Dependency-order assertions, cyclic/unknown-edge parser tests, exact BRD digest tests, fresh duplicate merge tests, and the earlier manual-Project-event mutation all pass against the restored implementation.
