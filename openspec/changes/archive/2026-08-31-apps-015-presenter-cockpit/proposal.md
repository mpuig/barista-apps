# apps-015: Deterministic presenter cockpit

## Why

The managed environment now preflights and wakes reliably, but a presenter
still creates issues manually, reconstructs workflow state across tabs, and has
no safe reset boundary. That operational choreography is the largest remaining
source of demo error.

## What changes

- Add a public, read-only `/presenter` cockpit backed by Factory's authoritative
  program, attempt, event, artifact, and deployment records.
- Add presenter-token-protected launch and reset actions.
- Launch the reviewed deployment-status scenario idempotently and converge
  duplicate clicks/reloads on one root issue.
- Permit reset only after terminal settlement; refuse to disguise unsupported
  cancellation as cleanup.
- Show the next human action, dependency gates, live attempts, pull requests,
  deployment health, and rehearsal links in one presentation-focused view.
- Publish concise 3–5 minute and complete 10–15 minute step-by-step runbooks.

## Boundaries

Product stage interpretation stays in Apps. Cloud remains a generic activity
and intent service. The presenter token is a distinct authority and never
appears in public configuration, HTML, state JSON, logs, or reports. GitHub
merge approval and Cloud deployment intent remain manual human actions.
