# Product-program SDLC

## Why

The issue SDLC proves that a bounded GitHub request can become an independently verified draft pull request. A product demo must prove the larger human-controlled loop: clarify an incomplete brief, publish a reviewable BRD, treat a human merge as approval, derive a bounded dependency plan, implement features from current `main`, and accept the assembled product.

GitHub Projects can make that flow visible, but project cards are mutable presentation data and cannot be workflow authority.

## What changes

- Add typed Factory operations for product briefs, feature plans, and final program acceptance.
- Add closed BRD, feature-plan, dependency, approval, and program-result protocols.
- Extend the Apps-owned GitHub controller with durable program state, merged-BRD correlation, idempotent feature issue delivery, dependency release, and final acceptance.
- Add optional, controller-owned GitHub Projects projection with durable retryable failures and corrective reconciliation.
- Add stage-specific deterministic reference workers and boundaries for separately credentialed model-backed workers.
- Add offline standalone acceptance for the complete product-program flow.

## Boundaries

This change is entirely in `barista-apps`. It does not add GitHub, BRD, feature, program, planning, or SDLC concepts to Core or Cloud. Existing generic app-run, session, grant, artifact, event, log, source, and forge protocols are reused unchanged.

## Compatibility

Existing issue-only webhook workflows, manifests, direct SDK calls, and controller deployments remain valid. Product programs and GitHub Projects projection are opt-in.
