# Barista Apps documentation

The open userland for [Barista](https://github.com/mpuig/barista.sh): App
Manifest, Host API, conformance, local provider, SDK, and reference apps.

- [Architecture](architecture.md) — the kernel / provider / app layering and
  the three trust seams.
- [Standalone install](standalone-install.md) — run the whole stack offline,
  with Barista Cloud unreachable.
- [Writing a provider](providers.md) — implement the Host API; capability
  profiles; prove it with the conformance suite.
- [Authoring an App Manifest](manifest-authoring.md) — digest pinning,
  reference-only secrets, least-privilege permissions, capabilities.
- [Running typed app operations](app-runs.md) — canonical App Runs, lifecycle
  observation, verified results, detach, and cleanup.
- [Managed demo smoke gate](managed-acceptance.md) — default, model, and slow
  release profiles with bounded machine-readable evidence.
- [Source and forge adapters](source-and-forge-adapters.md) — immutable Git
  acquisition, bounded objectives, patches, branches, and explicit draft delivery.
- [GitHub Factory controller](../integrations/github-factory-demo/README.md) —
  signed issue webhooks, bounded clarification, approved briefs,
  dependency-gated feature runs, independent acceptance, generic activity, and
  explicit source-owned deployment.
- [Threat model](threat-model.md) — untrusted apps, delegated grants,
  exact-memory secrets, malicious capsules, stories, adapters, local
  remote-binding, and supply chain.

## Repository map

| Path | What |
|---|---|
| `contracts/` | App Manifest, App Run, Host API (OpenAPI + streaming), and Session Story schemas + golden tests |
| `conformance/` | Black-box provider conformance suite (+ standalone guard) |
| `providers/local/` | Single-user reference Host API provider over `barista.sh` |
| `sdks/python/` | Provider-neutral SDK + adapter interface |
| `apps/{pi,claude,codex}/` | Harness adapters |
| `apps/{change-agent,factory,github-issue-triage,github-issue-worker,github-product-worker,lift,story}/` | Portable and internal apps |
| `integrations/github-factory-demo/` | Signed GitHub controller, durable product programs, generic activity projection, and trusted deployment adapters |
| `acceptance/` | Offline acceptance plus the explicit managed demo smoke release gate |
| `scripts/supply_chain_check.py` | Digest/reference/lock/schema-drift checks |
