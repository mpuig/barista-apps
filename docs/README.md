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
- [Source and forge adapters](source-and-forge-adapters.md) — immutable Git
  acquisition, bounded objectives, patches, branches, and explicit draft delivery.
- [GitHub Factory demo](../integrations/github-factory-demo/README.md) — signed
  issue webhooks, one ephemeral Factory run, independently verified draft PRs,
  durable deduplication, and teardown.
- [Threat model](threat-model.md) — untrusted apps, delegated grants,
  exact-memory secrets, malicious capsules, stories, adapters, local
  remote-binding, and supply chain.

## Repository map

| Path | What |
|---|---|
| `contracts/` | App Manifest, Host API (OpenAPI + streaming), Session Story schemas + golden tests |
| `conformance/` | Black-box provider conformance suite (+ standalone guard) |
| `providers/local/` | Single-user reference Host API provider over `barista.sh` |
| `sdks/python/` | Provider-neutral SDK + adapter interface |
| `apps/{pi,claude,codex}/` | Harness adapters |
| `apps/{change-agent,factory,github-issue-worker,lift,story}/` | Portable and internal apps |
| `integrations/github-factory-demo/` | Signed GitHub webhook controller and bootstrap tooling |
| `acceptance/` | Full offline acceptance flow (Cloud blocked) |
| `scripts/supply_chain_check.py` | Digest/reference/lock/schema-drift checks |
