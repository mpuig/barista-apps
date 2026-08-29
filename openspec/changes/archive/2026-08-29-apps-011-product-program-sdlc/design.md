# Design: product-program SDLC

## Ownership and trust

The persistent GitHub controller is the source of truth for product-program workflow state. GitHub issues, comments, pull requests, BRDs, plans, Project fields, and webhook payloads are untrusted data. Human authority is configured by the controller and is never inferred from issue text or Project card movement.

Factory and worker sessions are ephemeral app runs. They return bounded canonical artifacts and verification evidence. The controller owns all GitHub delivery credentials and independently verifies every external side effect. A Factory may open a verified draft PR through runner-owned delivery, but cannot approve or merge its own work.

## State machine

A program begins from a configured repository issue. A BRD worker returns `needs_input`, `refused`, or a canonical BRD proposal. Clarification ends an attempt; an authorized comment creates a fresh correlated attempt. A verified BRD becomes a draft PR. Only an authorized `pull_request.closed` event with `merged=true`, matching repository, program, base, PR, head identity, and expected BRD digest records approval.

The planner consumes the immutable approved BRD bytes and commit. Its bounded feature graph must have unique stable feature identities, no unknown edges, and no cycles. The controller idempotently publishes issues and records receipts. A feature becomes runnable only when every predecessor has a correlated accepted merge. Each implementation starts from current trusted `main`, avoiding stacked branches initially.

After every required feature is merged, an acceptance worker checks the assembled exact commit without Host API, forge, model, or project authority. The controller records the final canonical result.

## GitHub Projects projection

Projects v2 is an optional read model. A separate controller-only token may add the program and feature issues to one configured project and write bounded fields such as Type and Status. The adapter reads no workflow instructions from project fields.

Projection happens after durable canonical transitions. Successes and sanitized failures are persisted. Failures are retryable and normally do not block BRD, planning, implementation, delivery, or acceptance. Startup reconciliation derives desired fields from SQLite and overwrites manual drift. Project credentials never enter app-run envelopes, objectives, argv, clone URLs, worker environments, results, or webhook responses.

## Idempotency and recovery

Stable identities cover webhook delivery, program, attempt, clarification, BRD artifact, BRD PR, approved commit, feature, dependency edge, feature issue, implementation attempt, feature PR, result, external delivery, and project item. SQLite migrations are additive. Terminal and coordinator sessions retain the existing bounded result-handoff behavior; failed integrity checks preserve sessions for forensics.

## Limits

Canonical documents have closed schemas and explicit byte, item, depth, string, and URL bounds. Dependency plans are acyclic. Repository scope, trusted base, allowed operations, checks, responder/approver policy, commands, credentials, delivery targets, and project configuration come only from trusted controller configuration.
