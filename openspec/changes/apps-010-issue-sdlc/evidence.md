# Verification evidence

## Implemented slice

- Closed canonical `v1alpha1` triage decisions with `ready`, `needs_input`, and `refused` variants, a 64 KiB document cap, field/count bounds, digest identity, and secret scanning.
- Signed `issue_comment.created` ingress remains behind the existing exact-byte HMAC and repository/issue checks.
- SQLite now durably tracks workflow state, attempt, prior Factory result, question digest, answer comment, and comment delivery disposition. Existing databases migrate additively and legacy accepted attempts remain recoverable.
- Only configured responders can advance `awaiting_input`; duplicate, stale, bot, marker, self, oversized, empty, and secret-bearing answers are inert or refused.
- Fresh attempts use `github-<repo>-issue-<number>-attempt-<n>` while publication keeps `barista/issue-<number>`.

## Checks

- Factory: 73 passed.
- GitHub controller: 30 passed.
- Deterministic issue worker: 5 passed.
- Standalone acceptance: 1 passed, 3 managed tests skipped, 1 opt-in test deselected.
- Supply-chain and strict OpenSpec checks passed.
- Draft PR #46 required CI passed on the current revision (15 jobs).

## Pending

Factory child triage execution, the typed `issue-sdlc` operation, independently verified question publication, ready-path implementation composition, failure questions, and full conversational acceptance remain incomplete. The PR stays draft and no beta deployment is authorized from this evidence.
