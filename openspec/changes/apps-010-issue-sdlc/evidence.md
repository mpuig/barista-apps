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
- GitHub controller and offline acceptance: 34 tests passed, including unclear → verified question → authorized answer → fresh attempt → verified draft, wrong-target refusal, deduplication, and database migration.
- Deterministic triage worker: 6 tests passed.
- Deterministic implementation worker: 6 tests passed.
- Standalone acceptance: 1 passed, 3 managed tests skipped, 1 opt-in test deselected.
- Factory and triage Docker images built successfully from the repository root.
- Supply-chain, deployment-script syntax, and strict OpenSpec checks passed.

## Mutation evidence

- Inverted the independently checked question-delivery target comparison. `test_wrong_question_target_refuses_comment_and_preserves_session` failed because the dishonest result posted instead of raising; the comparison was restored and the named test passed.
- Allowed `needs_input` to enter the ready implementation branch. `test_needs_input_stops_before_implementation_and_returns_verified_question` failed when an implementation worker was created; the closed state branch was restored and the named test passed.

## Pending managed acceptance

Build and install reviewed digest-pinned Factory, triage, and implementation worker images with the controller update, then run one disposable real-GitHub clarification flow. No model credential is needed for the deterministic reference acceptance.
