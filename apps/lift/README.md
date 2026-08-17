# apps/lift

**Lift** moves an agent session between hosts: **exact** execution transfer when
the source is Barista-managed and compatible, honest **semantic** continuation
otherwise. It never silently substitutes semantic for a requested exact
transfer, and it preserves the source until the target is accepted.

- **Manifest:** [`manifest.json`](manifest.json)
- **Spec:** `openspec/changes/apps-001-portable-agent-apps/specs/lift-app/spec.md`

## Modes

- **exact** — requires a Barista-managed compatible capsule; preserves memory,
  process, disk, and lineage. Refuses (with an explanation) when the source is
  not managed or the target cannot restore it — no cold-boot or semantic
  fallback.
- **semantic** — starts a new process from an adapter bundle (workspace, VCS,
  transcript, skills, tools, environment, continuation prompt) and reports which
  components transferred and which are missing.
- **auto** — prefers exact; falls back to semantic only with explicit
  confirmation (`ConfirmationRequired`).

## Guarantees

- **Source preserved until acceptance.** Export → verify → import →
  compatibility → prepare → *accept* → only then optionally pause/delete the
  source. Any failure leaves the source usable and records resumable transfer
  state.
- **Every Lift emits a receipt** — source/target provider, mode, content and
  lineage ids, adapter and versions, compatibility, transferred/missing
  components, target acceptance, and source disposition — with no secret values.

## Status

The semantic path is complete and runs against any conformant provider through
the SDK adapter interface. The exact path's orchestration, compatibility
handling, acceptance gate, and source-preservation logic are complete and tested
against a `CapsuleClient` abstraction; the concrete client over the Host API
**capsule endpoints** lands with the kernel capsule work (`barista-046`) and the
corresponding Host API additions. Until then, exact Lift against the current
local provider refuses honestly (the provider does not advertise exact capsule
transfer).

## Tests

```bash
cd apps/lift && uv run --extra test pytest -q
```

Covers exact compatible transfer, exact incompatibility, native semantic
transfer, interrupted upload, target rejection, source preservation,
exact-only-no-fallback, and auto-with-confirmation.
