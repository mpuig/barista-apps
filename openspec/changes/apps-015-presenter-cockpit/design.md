# Design

## Control and authority

`BARISTA_DEMO_PRESENTER_TOKEN` is optional, high-entropy, and distinct from
webhook, forge, Project, activity, Host API, and deployment authorities. The
cockpit is always readable. Mutations require an exact bearer token comparison;
the static client accepts it manually and keeps it in session storage only.

Launch uses the controller's existing repository-scoped forge authority. A
bounded idempotency key is written into an inert issue marker. A durable
`demo_scenarios` row and GitHub marker converge repeat clicks and service
restarts. At most one unreset scenario is current; a presenter must explicitly
reset a terminal scenario before launching another.

Reset closes the terminal root issue and marks only the cockpit scenario reset.
It requires the program and all attempts to be terminal. An active scenario
returns a structured conflict with its next human action. The action does not
claim to cancel Host API execution because that protocol does not yet provide a
bounded cancellation guarantee.

## Read model

The public state endpoint contains no credentials. It returns current scenario,
up to 20 recent scenarios, authoritative programs, bounded attempts/events,
and credential-free links. Product-specific stage and next-action derivation is
performed in Apps. The browser polls with no-store semantics and updates text
through DOM APIs rather than injecting server content as HTML.

## Visual direction

The cockpit extends Cloud's established operator world: porcelain/espresso
surfaces, crema accents, semantic status colors, compact workhorse typography,
and receipt-like exact identities. It is composed as a presenter rundown rather
than a generic dashboard: one dominant stage line, a left-to-right delivery
rail, a dependency workbench, and a compact evidence ledger. Motion is limited
to live-state emphasis and disabled under reduced-motion preferences.
