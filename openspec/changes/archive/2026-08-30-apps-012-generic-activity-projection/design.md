# Design: Generic activity projection from applications

## Generic seam

The publisher targets a generic HTTPS activity API. It knows the envelope but not Cloud internals. Factory maps its own semantics at the application boundary: program states become canonical phases, GitHub objects become labeled links, commits and result digests become artifacts, and durable transitions become ordered generic events.

Cloud receives no BRD parser, dependency engine, forge token, Project token, Host API token, or workflow authority. The activity token is a separate mode-bounded credential and equality with any existing authority is refused.

## Delivery

A single bounded background publisher serializes projection. `activity_projections` stores each program's desired canonical document, monotonically increasing revision, last published digest, attempts, and bounded error. State changes only update desired projection; startup and retries reconcile from SQLite. The workflow never waits for projection success.

## History mapping

Stable events cover issue creation, clarification, BRD publication and approval, plan validation, feature issue publication, feature PR verification and merge, final acceptance, and failure. Every event contains source-owned text plus explicit links. Repository, issue, PR, Project, and exact commit URLs remain ordinary HTTPS links. Digests and commits are artifacts, not action authority.

## Actions

An accepted program advertises a generic `deploy` action only as available when a trusted deployment adapter has been configured outside source data. The controller polls with its narrow source credential, records a stable request/operation identity, revalidates accepted program state, and invokes one fixed absolute argv with a bounded canonical document on stdin. The adapter receives a minimal non-secret environment and must return a bounded exact result containing a digest-pinned image, service identity, and credential-free HTTPS endpoint. Retries reuse the request identity; durable source state closes a result-handoff crash without repeating a completed transition. Cloud records intent and result but never executes it. The action is visibly unavailable when no trusted runner exists.
