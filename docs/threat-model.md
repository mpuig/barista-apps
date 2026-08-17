# Threat model

Scope: the open Barista userland — App Manifest, Host API, local provider, SDK,
adapters, and the Factory/Lift/Story apps. Barista Cloud's tenancy, billing, and
global registry are out of scope (proprietary). The kernel's isolation
guarantees are `barista.sh`'s.

## Trust boundaries

- **Kernel ↔ provider.** The provider holds privileged node credentials; apps
  never do. A provider must not expose node addresses, node credentials,
  object-store credentials, or database ids across the Host API.
- **Provider ↔ app.** An app is untrusted controller code holding delegated,
  scoped grants. It acts only through the Host API actions its manifest
  declares.
- **App ↔ harness.** Adapters handle untrusted, harness-native bytes and treat
  them as opaque.

## Assets and threats

### Untrusted apps
- **Threat:** an app tries to exceed its declared permissions (create unbounded
  children, read other sessions, escalate).
- **Mitigation:** least-privilege manifest actions; the provider authorizes each
  action; `child_sessions` bounds fan-out; Factory workers get a strictly
  narrower grant (no `session.create`).

### Delegated grants
- **Threat:** a grant becomes an ambient, long-lived API key or is copied.
- **Mitigation:** grants are resource/action/expiry-scoped; workers receive
  secret **references**, never raw values; the provider resolves references at
  the boundary. (Provider-side grant enforcement is the `grants.delegated`
  profile; see the kernel's execution-epoch work for rebinding on restore/fork.)

### Exact-memory secrets
- **Threat:** a capsule (exact memory) contains secrets the workload copied into
  RAM; forking/exporting duplicates them.
- **Mitigation:** capsules are treated as **secret-bearing** and private by
  default. Only platform-mediated, epoch-bound grants receive the safe-rebind
  guarantee — the kernel does **not** claim to scrub arbitrary workload memory.
  Lift's exact mode never exports a capsule to a public/story surface.

### Malicious capsules
- **Threat:** a tampered or incompatible capsule imported at a target.
- **Mitigation:** content-addressed capsules; verify-then-register; compatibility
  preflight; **no cold-boot fallback** for an exact restore. Lift refuses an
  incompatible capsule and preserves the source.

### Stories
- **Threat:** a published story leaks secrets or grants execution.
- **Mitigation:** deterministic, versioned redaction that **fails closed** on a
  residual high-confidence secret or unknown media; stories contain no capsule
  object, writable filesystem, bearer grant, secret value, or executable field
  (enforced structurally and by an explicit non-executability check). Provenance
  is verifiable; pseudonymization mints a new story id without altering record
  digests.

### Adapters
- **Threat:** an adapter writes raw model-provider credentials into a manifest,
  log, story, or semantic bundle.
- **Mitigation:** adapters request named grants/references only; the SDK
  `sensitive` helpers reject raw declared secret values; semantic bundles carry
  references/redactions, and native bytes stay opaque (not scanned, not
  published as executable).

### Local remote-binding
- **Threat:** the single-user local provider is exposed as an unauthenticated
  remote server.
- **Mitigation:** default bind is a user-owned Unix socket; a non-loopback bind
  requires `--allow-remote` **and** `--token`. The provider makes no
  multi-tenant claims.

### Supply chain
- **Threat:** a mutable image tag, unpinned dependency, or unsigned schema/app
  is substituted.
- **Mitigation:** manifests are digest-pinned (enforced by
  `scripts/supply_chain_check.py`); each package pins the SDK by path source and
  ships a `uv.lock`; contract schemas have golden content-id tests; the
  standalone conformance profile forbids proprietary imports and Cloud network.

## Non-goals

- Detecting or removing arbitrary secrets a workload copied into its own memory.
- Replacing the kernel's isolation or the provider's tenancy controls.
- Making the local provider a hardened multi-tenant service.
