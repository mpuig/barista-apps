# apps-015 evidence

## Reviewed implementation

- Presenter cockpit and controls: <https://github.com/mpuig/barista-apps/pull/76>
- Browser-enforced nonce CSP: <https://github.com/mpuig/barista-apps/pull/77>
- Correct beta Host API credential default: <https://github.com/mpuig/barista-apps/pull/78>
- Clean reset state and absent-link handling: <https://github.com/mpuig/barista-apps/pull/79>
- Planned-feature rendering correction: <https://github.com/mpuig/barista-apps/pull/80>
- Final deployed source: `5c2a12e2ee5a552c2ece31010de25ddce55e500e`

All required CI jobs passed before each merge. Local validation included 74
GitHub Factory integration tests, 11 acceptance tests with four expected
offline skips and one slow deselection, 74 contract tests, supply-chain checks,
Ruff, strict OpenSpec, and `git diff --check`. The mechanical design detector
returned no findings. Desktop and mobile browser captures are retained under
`.impeccable/review/`.

## Managed deployment

The reviewed additive deployment preserved the root-owned environment and
runtime state, rebuilt/published the beta-local workloads, installed the
controller unprivileged, and recorded source revision
`5c2a12e2ee5a552c2ece31010de25ddce55e500e`. The generated presenter token is
mode `0600` locally; the reconstructed controller environment is root-owned
mode `0600` and traveled through SSH stdin. Its value appeared in no command,
response, HTML, state document, report, or log.

Managed checks proved:

- public cockpit, controller, Cloud, and retained Program 21 returned HTTP 200;
- `/presenter` was `no-store`, frame-protected, and contained a per-response
  nonce meta policy enforcing no default resources, nonce-only script/style,
  same-origin connect/form, and no `unsafe-inline`;
- public state advertised controls without containing presenter authority;
- a launch created exactly issue 29/program 29 and exact-key replay returned the
  same scenario without another issue;
- a terminal reset closed issue 29, durably retained evidence, and allowed a new
  launch;
- issue 30/program 30 reached clarification, accepted the authorized answer,
  retained a subsequent transient Git-ref resolution failure, and reset
  terminally without claiming execution cancellation;
- after both resets, `current_scenario` was null and the cockpit returned to
  **Ready for a clean run**;
- selecting retained Program 21 rendered all three dependency-gated features,
  exact evidence, BRD, Activity, Project, and deployed-product links;
- no absent PR/deployment values became `/null` links;
- the controller, Cloud, and Program 21 remained healthy after all exercises.

Program 29's initial `grant.not_recognized` failure exposed the beta provisioner
still defaulting to the generic local key. PR #78 corrected it to the established
appliance tenant key and beta was reprovisioned without exposing the value.
Program 30's retained failure is honest environmental evidence, not a green
end-to-end claim; successful full product evidence remains Program 21. Failed
sessions remain bounded forensic evidence by policy.

## Presenter documentation

`integrations/github-factory-demo/DEMO_RUNBOOK.md` now contains:

- an explicit before-audience preflight;
- a 3–5 minute no-spend route through retained Program 21;
- a complete live route using the cockpit as the primary screen;
- exact clarification, BRD merge, feature dependency, acceptance, deployment,
  and terminal reset steps;
- a recovery table that never recommends bypassing authority or hiding failure.

`PRODUCT.md`, `DESIGN.md`, and the presenter surface brief preserve the product,
authority, accessibility, and visual decisions for future work.
