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

## Managed product-program acceptance

Apps PR #54 merged as `f1e8c06b418b8a165559851d643dec6ff9fc64ef`; all 15 required CI jobs passed. A clean remote-`main` deployment built and registry-verified:

- Factory: `sha256:7f026c5e75a8fa8cb33c83e1453df038f74d5aad527be4ca01b4d04ebd8af19c`
- BRD/planner/feature worker image: `sha256:235ccbae78a19afc88ceb3292a2698d82d9305cc4d80fa0be8c979b3f886635f`
- Triage remained `sha256:c14ec78e22c4fcb412bce23b4f2b5369b9719acf947910875122c1e2f79bfbbd`
- Issue worker remained `sha256:ecb2846af6ddfa4582bfb39ccc53fdcd0f9a5d9b274ab54bad7376b82ecf013b`

The initial managed run exposed two live-only integration gaps without publishing feature work:

1. Program #7 reached its verified BRD PR, but that merge predated reconciliation of `pull_request` into the historical webhook subscription. It remains inert at `awaiting_brd_merge`; the webhook now has exactly `issues`, `issue_comment`, and `pull_request` events.
2. Program #9 completed clarification and correlated BRD approval, then failed closed because GitHub does not advertise an arbitrary commit SHA to `git ls-remote`. Its failed planning owner was retained while logs proved the exact `TerminalError`; after evidence collection it was explicitly reaped. No feature issue or feature delivery was created. Apps PR #55 restored trusted-`main` resolution followed by exact expected-commit comparison, allowed explicit bootstrap reuse to preserve—not overwrite—historical seed files, passed all 15 CI jobs, and merged/deployed as `d263bf01cedbbb7b413894953357dd6b0bba7926`.

### Successful public program

- Root issue: <https://github.com/mpuig/barista-factory-demo/issues/11>
- Clarification question: <https://github.com/mpuig/barista-factory-demo/issues/11#issuecomment-5461026578>
- Authorized answer: <https://github.com/mpuig/barista-factory-demo/issues/11#issuecomment-5461027344>
- Verified BRD result: `sha256:8ddd809fab80e5fb89d44ac51e9e3c57b0ad1748bb842b2a55106bc2d2f11904`
- BRD PR/approval: <https://github.com/mpuig/barista-factory-demo/pull/12>, approved merge `d55f6fd89ae9a4a4faa8ea3048be0d6daac8c03b`
- BRD digest: `sha256:1d602f25ecae7e3c6a41d448ff0d34b64dd2ac1a32aeea42286916689ca3a16e`
- Canonical feature plan: `sha256:d8bdf4a7c0dd729fb70ad33a49a64e206817bbae1b9b5897ca78a932fbf50538`
- Feature-plan Factory result: `sha256:c483eeff51a973a60022b2359a965bf5fb0bc7a54dedf13b55bba126085202d6`

Dependency-gated feature evidence:

| Feature | Issue / PR | Base → verified head → merged commit | Factory result / patch |
| --- | --- | --- | --- |
| `status-api` | [#13](https://github.com/mpuig/barista-factory-demo/issues/13) / [#16](https://github.com/mpuig/barista-factory-demo/pull/16) | `d55f6fd89ae9a4a4faa8ea3048be0d6daac8c03b` → `124a83d6501050fdc9bb4e123e663f33206acd7d` → `3de302992eeeb8d478e9fb47dc2ec0fbbe64d38f` | `sha256:331a99c5107877a82e93a9c49d575bbe5f36a0bb6fb2f3858e116e990f0dd5f1` / `sha256:092d621e63df8a980c5842b60e8a06c5d35e0ffd6b9f0e51688a1f3a0e2557c0` |
| `event-store` | [#14](https://github.com/mpuig/barista-factory-demo/issues/14) / [#17](https://github.com/mpuig/barista-factory-demo/pull/17) | `3de302992eeeb8d478e9fb47dc2ec0fbbe64d38f` → `a4c26f0015492582179dbd8c0f5a49057d32f5e7` → `68f91d8056a70debe9988c37e4386ca265fb65a1` | `sha256:8d47a023270d9ad156596d9a6826669fd03bf41ef82efb0d83f5da0e534f4296` / `sha256:a4b688930b7b8818f85a498abd741fee602fbe77c9d6d59257be46b648cbc71f` |
| `dashboard` | [#15](https://github.com/mpuig/barista-factory-demo/issues/15) / [#18](https://github.com/mpuig/barista-factory-demo/pull/18) | `68f91d8056a70debe9988c37e4386ca265fb65a1` → `ceaa41e09eb867bc34ede77dde04827f0f598166` → `4e0843adcaf27bf821d93d867ee8331be9f926d2` | `sha256:a0ca653b4efd8084e29c2f7130d208e0efb99014d06e74a4676dd9977987e1ed` / `sha256:3bceb2837ba338c816da053d622572511d6d788ee89a904337ba87341db4a2cf` |

Final acceptance bound exact assembled commit `4e0843adcaf27bf821d93d867ee8331be9f926d2`, all three feature identities, exit code zero, and command digest `sha256:4bccb78dba1637183e68be76b9302a848a6d3c4a866785c76ecd54eed73e7f36`. Its Factory result is `sha256:22329b11b65a1854b26a784ccb4778c40249d526d032367c80dd0e0421d4ee62`. All successful owning and child sessions were absent after bounded result handoff and cleanup.

The assembled repository was then resolved from trusted `main`, required to equal that exact commit, built on the managed Hetzner node, pushed to the loopback registry, and run by digest `sha256:3298428e9ab924c23daa4727ed6ad9afeab7b303da897ad15e03a7708b17c887`. One runtime container served health, event API, and compiled dashboard assets. An event survived container removal and recreation through the mode-0700 SQLite binding. The acceptance container and temporary state were removed afterward; the digest remains available in the managed registry.

Public Project #4 returned HTTP 200 and converged the program plus all three features to `Done`. Durable projection details include controller-owned work type, program, feature, dependency, result, and exact PR URI with no error. The anonymous board screenshot is [artifacts/program-11-project.png](artifacts/program-11-project.png). Production source marker, systemd health, `/etc/barista` mode 0711, root-owned environment mode 0600, and the three-way webhook subscription were verified after deployment.
