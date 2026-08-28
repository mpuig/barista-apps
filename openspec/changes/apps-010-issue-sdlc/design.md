# Design: resumable issue-driven SDLC Factory

## D1 — Human waiting is a sequence of ephemeral attempts

The persistent signed-webhook controller owns durable issue state. Each attempt launches one Factory App Run and terminates after one of three outcomes: `needs_input`, `refused`, or a verified implementation result. A later authorized issue comment creates attempt N+1 with immutable links to the prior result and answer delivery. No paused session or expiring delegated grant spans the human wait.

Stable identities are `github-<repo>-issue-<number>-attempt-<n>`; the publication branch remains `barista/issue-<number>`. Retries of one attempt reuse its content/run identity. A new answer advances the attempt exactly once.

## D2 — Triage is a child worker with a closed result protocol

Factory launches a separately installed triage worker in an isolated checkout. Its trusted command is selected by configuration, not issue text. It receives a bounded objective document containing the issue and authorized answer history and writes a bounded canonical decision:

- `ready`: summary plus explicit acceptance criteria;
- `needs_input`: one to five focused questions;
- `refused`: a policy-neutral reason code and message.

Factory validates the schema, bounds, UTF-8, and absence of high-confidence secrets. Worker claims do not alter trusted implementation commands or acceptance checks.

## D3 — Ready decisions feed the existing software-change boundary

For `ready`, Factory runs configured implementation workers against the exact base and then runs coordinator-owned acceptance without Host API, forge, or model credentials. The triage summary and criteria are data available to workers, not shell fragments. Existing patch integration, secret scan, moving-base checks, result registration, and runner-owned draft delivery remain unchanged.

## D4 — Questions and failures are explicit runner-owned deliveries

The App Run declares two possible runner-owned deliveries: draft change and issue comment. A `needs_input` result exposes a digest-identified canonical question document. A recoverable implementation/check failure exposes a bounded failure-question document; it never includes raw stderr, credentials, or arbitrary provider prose. The controller verifies kind, target, request digest, issue identity, and document digest before posting an idempotent marker comment.

Terminal integrity/policy failures publish nothing.

## D5 — Only authorized answers advance

The controller accepts only signed `issue_comment.created` events for the allowlisted repository and an issue already in `awaiting_input`. The webhook's commenter login must match the configured responder allowlist; the initial reference deployment defaults to the repository owner. Bot comments and the controller's own marker comments never advance state. Delivery ID and comment ID are both deduplicated.

The answer body is inert context. It cannot replace the original issue or any trusted run field.

## D6 — Approval means verified-for-review

Success opens a draft pull request and posts exact evidence. Factory may report `verified_for_review=true`; it does not submit a GitHub APPROVE review and does not merge. Human/policy approval is a separate authority and audit event.
