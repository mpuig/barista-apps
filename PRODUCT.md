# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Barista Apps serves application authors and technical operators. The GitHub
Factory demo also serves a presenter who must launch, explain, and recover a
live product-development workflow while an external audience follows its
progress.

## Product Purpose

Barista Apps defines portable agent applications and composes them into
reviewable workflows over Barista's universal Host API. Success means source
work is isolated and independently verified, human authority remains explicit,
and managed demonstrations are repeatable without leaking credentials or
leaving disposable compute behind.

## Positioning

Factory turns untrusted product objectives into dependency-gated, exact-commit
software delivery while keeping planning, source mutation, approval, runtime,
and deployment authorities separate. Apps owns those product semantics; Core
and Cloud do not need to understand BRDs, features, pull requests, or releases.

## Operating Context

Work starts in GitHub issues and comments, proceeds through draft pull requests
and human merges, and is projected to a GitHub Project and Cloud's generic
activity stream. Presenters use a preflight report, the Factory controller,
public GitHub pages, and the deployed product endpoint during a live demo.

## Capabilities and Constraints

- `$BARISTA_FACTORY_MISSION` is the canonical in-session mission mechanism.
- App Runs compile to existing Host API resources; there is no second scheduler.
- Model credentials remain provider-resolved references and never enter source,
  manifests, argv, reports, or logs.
- Product approval and deployment remain explicit human actions.
- Demo launch must be idempotent and bounded. Reset must not claim it canceled
  work when the underlying execution protocol cannot guarantee cancellation.
- Successful disposable sessions are deleted; bounded failed evidence remains.
- Cloud stores generic activity and human intent only; Factory interprets
  product-specific stages in Apps.

## Brand Commitments

The product is Barista. Operator experiences are precise, calm, candid about
state, and free of hype. Existing Cloud console terminology and its
porcelain/espresso operational register are the visual authority for adjacent
Apps-owned presenter surfaces.

## Evidence on Hand

- Managed acceptance and three no-spend rehearsal reports are documented in
  `openspec/changes/archive/2026-08-31-apps-014-demo-preflight/evidence.md`.
- The complete human workflow is documented in
  `integrations/github-factory-demo/DEMO_RUNBOOK.md`.
- Program 21 is a retained public accepted/deployed example.
- The controller's SQLite state is authoritative; GitHub Projects and Cloud
  activity are projections.

## Product Principles

1. Authority boundaries are part of the product, not implementation detail.
2. Independent evidence outranks worker-authored claims.
3. Human intent is explicit, attributable, and revalidated at execution time.
4. Repeatability means stable identity, bounded retries, and honest cleanup.
5. Presenter UX exposes progress and next actions instead of transient protocol
   failures or fabricated certainty.

## Accessibility & Inclusion

Operational surfaces must remain keyboard accessible, responsive, readable
without animation, and understandable without relying on color alone.
