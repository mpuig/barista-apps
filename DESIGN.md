# Barista Apps design system

<!-- impeccable:design-schema 1 -->

## Direction

Apps-owned operator surfaces extend Barista Cloud's console register rather
than inventing a separate product identity. The physical reference is a clean
service counter and its exact printed ticket: porcelain and espresso fields,
crema emphasis, compact factual labels, and identities set like receipt data.
The interface is calm but not passive; semantic state is visible immediately.

## Color

Use perceptual `oklch()` values and tinted neutrals.

- Background: porcelain `oklch(97% 0.006 80)`.
- Raised surface: `oklch(99.4% 0.003 80)`.
- Recessed surface: `oklch(94.5% 0.009 76)`.
- Primary ink: espresso `oklch(24% 0.018 55)`.
- Secondary ink: `oklch(39% 0.017 55)`.
- Accent: crema `oklch(68% 0.13 70)`; darker accent for text.
- Success: green `oklch(57% 0.13 153)`.
- Waiting: blue `oklch(61% 0.09 246)`.
- Failure/refusal: red `oklch(56% 0.17 25)`.

Dark mode keeps the hue relationships and increases text lightness. Color never
carries state alone; every state also has a text label.

## Typography

Operational UI uses a workhorse grotesk stack. Use the Barista-hosted Schibsted
Grotesk where the deployment already serves it; isolated integration pages use
`Aptos`, `Segoe UI`, then sans-serif without external requests. Exact commits,
digests, run names, and timestamps use the platform monospace stack. Headings
are tightly tracked but never below `-0.04em`; body text remains normal width
and readable.

## Composition

Prefer one dominant statement and a visible sequence over a metric-card grid.
Group operational detail with hairlines and whitespace rather than nested
cards. The presenter cockpit uses:

1. a dominant current-stage statement;
2. one six-step authority rail;
3. a dark next-action field;
4. a dependency workbench;
5. a narrow exact-evidence ledger.

At narrow widths the page becomes one column, controls become a three-action
row, and the six stages wrap into two complete rows rather than disappearing.

## Components and states

- Corners are 9–12px; pills are reserved for compact state labels.
- Buttons have explicit normal, hover, active, disabled, loading, and keyboard
  focus states. One primary action per decision context.
- Hairlines divide sequences and ledgers. Do not combine a border and decorative
  shadow on the same container.
- Use native dialogs only for protected focus such as credential entry or a
  destructive confirmation.
- Empty states explain what will cause content to appear.
- Live state uses a labeled dot; reduced-motion users receive no pulsing or
  decorative movement.

## Content

Use source-owned terminology: program, BRD, plan, feature, attempt, accepted
commit, deployment, and reset. Copy names the current authority and the exact
next action. Never say work was canceled, cleaned, healthy, or deployed unless
the corresponding authoritative transition has settled.

## Accessibility

Meet WCAG AA contrast, preserve keyboard order and visible focus, maintain
44px-ish action targets, support 320px-wide screens, respect reduced motion, and
never rely on color, animation, hover, or abbreviated identifiers alone.
