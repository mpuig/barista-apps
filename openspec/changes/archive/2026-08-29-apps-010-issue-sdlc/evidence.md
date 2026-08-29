# Verification evidence

## Implementation

- Factory declares a typed `issue-sdlc` operation with separate triage and implementation app identities, trusted argv, bounded answer history, repository/objective bindings, coordinator-owned acceptance, and explicit runner-owned question/change deliveries.
- Triage runs in an isolated child checkout at the coordinator-selected exact commit. Its canonical 64 KiB closed decision is independently bounded, parsed, secret-scanned, and content-addressed.
- `needs_input` and `refused` stop before implementation. `ready` context is passed as inert data to the existing isolated software-change workers, after which coordinator-owned acceptance remains credential-free.
- Worker and acceptance failures are the only recoverable implementation failures that produce a static sanitized failure question. Integrity, authority, repository, malformed-decision, and secret failures request no delivery and preserve forensic sessions.
- The controller independently verifies result state, issue/repository/base identity, declared question target and request digest, canonical question bytes/digest/size, question count, and secret safety before an idempotent marker comment.
- SQLite durably tracks workflow state, attempt, prior Factory result, question digest, bounded answer history, answer-comment identity, and comment delivery disposition. Existing databases migrate additively and legacy accepted attempts remain recoverable.
- Only configured responders can advance `awaiting_input`; duplicate, stale, bot, marker, self, oversized, empty, and secret-bearing answers are inert or refused.
- Fresh attempts use `github-<repo>-issue-<number>-attempt-<n>` while publication keeps `barista/issue-<number>`. Verified success reports `verified_for_review=true`, opens only a draft, and never approves or merges.
- A separately installed deterministic triage reference proves unclear, answered, ready, and refused behavior without model authority. A model-backed replacement remains optional and separately credentialed.

## Checks

- Factory: 78 tests passed, including triage stop/ready/refused, context propagation, recoverable acceptance failure, malformed decisions, independent checks, moving bases, and patch integrity.
- GitHub controller and offline acceptance: 35 tests passed, including unclear → verified question → authorized answer → fresh attempt → verified draft, wrong-target refusal, deduplication, database migration, and the opt-in live clarification exerciser.
- Deterministic triage worker: 6 tests passed.
- Deterministic implementation worker: 6 tests passed.
- Standalone acceptance: 1 passed, 3 managed tests skipped, 1 opt-in test deselected.
- Factory and triage Docker images built successfully from the repository root.
- Supply-chain, deployment-script syntax, and strict OpenSpec checks passed.

## Mutation evidence

- Inverted the independently checked question-delivery target comparison. `test_wrong_question_target_refuses_comment_and_preserves_session` failed because the dishonest result posted instead of raising; the comparison was restored and the named test passed.
- Allowed `needs_input` to enter the ready implementation branch. `test_needs_input_stops_before_implementation_and_returns_verified_question` failed when an implementation worker was created; the closed state branch was restored and the named test passed.

## Managed real-GitHub acceptance

Reviewed Apps revision `c56b1ca974280c41272a9e4de363191ec0f8c64d` was deployed additively after PR #47 passed all 15 required CI jobs. The controller health check reported the separate Factory, triage, and implementation app identities. Registry-returned image identities were:

- Factory: `127.0.0.1:5000/barista-factory:0.6.0@sha256:8283d65ab0a7d90f23f873d7e6c66109cd85de319a7a9d1da9d41259d505737b`
- Triage: `127.0.0.1:5000/barista-github-issue-triage:0.1.0@sha256:c14ec78e22c4fcb412bce23b4f2b5369b9719acf947910875122c1e2f79bfbbd`
- Implementation: `127.0.0.1:5000/barista-github-issue-worker:0.2.0@sha256:ecb2846af6ddfa4582bfb39ccc53fdcd0f9a5d9b274ab54bad7376b82ecf013b`

The disposable real-GitHub flow passed through an unclear issue, a verified correlated question, one authorized answer, a fresh second attempt, independent acceptance, and a verified draft pull request:

- Issue: `https://github.com/mpuig/barista-factory-demo/issues/5`
- Question: `https://github.com/mpuig/barista-factory-demo/issues/5#issuecomment-5460565839`
- Question digest: `sha256:c093d6f353ba87e4ec1c89ab1475255ce93fd68d39bd01d653c3eb87bad7fe5a`
- Authorized answer: `https://github.com/mpuig/barista-factory-demo/issues/5#issuecomment-5460566055`
- Draft PR: `https://github.com/mpuig/barista-factory-demo/pull/6`
- Run: `github-0853c04f40-issue-5-attempt-2`
- Factory result: `sha256:4a4a70ecc76050dcd188abc49ce527ab26c3d3d2368bd47e66362368b10ccb81`
- Integrated patch: `sha256:e7cd2b1b89f0da792a62e3c9dcc3f0bdf7da20e6f2b0769f638db8cfa5bb3cdb`
- Base: `42155f201a0da9db0e969d8cf9eb2907465337c5`
- Head: `33ac2c4914c3c2d72513bcd609547aa7a0c5556a`

GitHub reported the pull request open and draft with the exact base, head, branch `barista/issue-5`, and patch marker. The controller reported attempt 2 succeeded with one correlated answer and `verified_for_review=true`. No issue-5 Factory, triage, implementation, or acceptance sessions remained after successful collection and delivery.

An earlier issue-4 attempt encountered `HostAPIError: HTTP 502` after Factory deployment changed the shared `/etc/barista` directory to mode `0700`, preventing the unprivileged Cloud gateway from reading its mTLS CA. The failed attempt and triage session were preserved for bounded forensics. PR #47 changed deployment and provisioning to mode `0711` while retaining the controller environment at mode `0600`; its mutation test failed when restored to `0700`. After production repair, node-event ingestion resumed and the successful issue-5 acceptance above completed.
