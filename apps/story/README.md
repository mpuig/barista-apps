# apps/story

**Session Story** creates a deterministic, redacted, **non-executable** account
of session knowledge that can be shared independently from terminal or capsule
access. A story is knowledge to read — never code to run, and never a path to
restore, attach, exec, or fork the source.

- **Manifest:** [`manifest.json`](manifest.json)
- **Bundle schema:** `contracts/session-story/v1alpha1/schema.json`
- **Spec:** `openspec/changes/apps-001-portable-agent-apps/specs/session-story/spec.md`

## What it does

- **Selects and canonically assembles** records — events, decisions, commands,
  diffs, commits, receipts, evaluations, and explicit artifacts by digest — into
  a bundle that validates against the open Session Story schema.
- **Redacts deterministically** with a versioned policy: high-confidence secrets
  and PII are replaced with stable markers and reported by category. Redaction
  is stable (same records + policy → identical `story_id`) and **fails closed** —
  a residual high-confidence secret or an unknown required media type blocks a
  publishable story.
- **Guarantees non-executability** — a story contains no capsule object,
  writable filesystem, bearer grant, secret value, or executable capability;
  attempts to smuggle one are refused.
- **Keeps provenance verifiable** — identifies source app and record digests,
  supports detached signatures, and offers deterministic pseudonymization that
  mints a new story id while preserving unchanged record digests.
- **Ships a static viewer** — `render_html` emits a self-contained,
  script-free, fully-escaped page.

## Use it

```bash
cd apps/story
uv run barista-story build records.json \
  --created-at 2026-08-17T00:00:00Z --title "My session" \
  --out story.json --html story.html
```

## Tests

```bash
cd apps/story && uv run --extra test pytest -q
```

Golden coverage: determinism, redaction + category reporting, fail-closed on
unknown media and residual secrets, non-executability, pseudonymization,
sign/verify, schema validity, and a script-free viewer.
