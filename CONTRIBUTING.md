# Contributing to Barista Apps

Thanks for considering a contribution. This project is Apache-2.0 and
developed in the open, following the same
[OpenSpec](https://github.com/Fission-AI/OpenSpec) workflow as
[`barista.sh`](https://github.com/mpuig/barista.sh).

## Before you start

1. Read the active proposal under `openspec/changes/` before writing code —
   this repository has no implementation yet, only a spec.
2. Read `openspec/config.yaml` for this project's rules: requirements use
   SHALL and must be provider-neutral; agent-specific behavior belongs in
   adapters, never the Host API; every proposal states how it stays usable
   with Barista Cloud absent.

## The change workflow

```
proposal.md → design.md → specs/<capability>/spec.md → tasks.md
→ apply → conformance suite green → human review → archive
```

- One change, one coherent, end-to-end portable-app outcome.
- A task is complete only once its outcome exists and the standalone
  (Cloud-blocked) conformance profile still passes.
- `openspec validate <change-id> --strict` must pass before requesting review.

## Boundaries that are not negotiable

- No proprietary import, credential, or network dependency in the standalone
  profile — the conformance harness enforces this and treats a violation as a
  failing build, not a warning.
- No harness-specific (Pi/Claude/Codex-specific) field in `contracts/host-api`
  or `contracts/app-manifest`. Put it in the adapter package instead.
- Manifests and stories carry secret *references*, never plaintext secrets.

## Local setup

Each package under `contracts/`, `sdks/`, `providers/`, `apps/`, and
`conformance/` is independently versioned; see its own README once it exists.
