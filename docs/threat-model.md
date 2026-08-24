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
- **Mitigation:** least-privilege manifest actions, each **scoped** to the app's
  own session or to the sessions it creates — never wider; the provider
  authorizes each action; `child_sessions` bounds fan-out *and* declares what a
  child receives; Factory workers get a strictly narrower grant (no
  `session.create`, `allow_descendants: false`).
- **Residual risk:** a child's action set being a subset of the app's own is
  **not enforced by the manifest schema** — JSON Schema cannot relate the two
  lists. It is a provider obligation checked at install
  (`contracts/app-manifest/v1alpha1/rules.py`). A provider that skips that check
  will install an app that over-delegates to its workers, and the conformance
  case `grants.over_delegating_manifest_refused` is what catches it.

### Delegated grants
- **Threat:** a grant becomes an ambient, long-lived API key or is copied.
- **Mitigation:** grants are resource/action/expiry-scoped; workers receive
  secret **references**, never raw values; the provider resolves references at
  the boundary. (Provider-side grant enforcement is the `grants.delegated`
  profile; see the kernel's execution-epoch work for rebinding on restore/fork.)

### Keeping a grant alive without making it permanent
- **Threat:** a credential that must outlive its own expiry becomes either a
  permanent one (extend the expiry and a leaked secret is good forever) or a
  minting privilege (let the holder ask for a new grant and it is a key that
  makes keys).
- **Mitigation:** `POST /v1alpha1/grants/refresh` **rotates** — the previous
  secret stops working, so a leaked one is worth only until the next refresh —
  and takes **no request body**, so the replacement's resource and actions are
  copied from the provider's record and there is no input a holder could widen
  with. Expired, revoked, already-rotated and **session-unbound** grants are all
  refused: the session is what bounds a refresh chain, so deleting a session
  revokes the grants bound to it, and a grant with no session has nothing to end
  its chain. Conformance cases
  `grants.refresh_preserves_exactly_the_presented_scope`,
  `grants.refresh_rotates_the_previous_secret`,
  `grants.refresh_cannot_widen_authority`,
  `grants.refresh_refused_after_revocation` and
  `grants.refresh_refused_after_expiry` are what catch a provider that gets any
  of this wrong — including one that reads scope from the request, which is
  issuance under refresh's name.
- **Residual:** a caller that refreshes and loses the response is locked out, by
  design. Factory reports that as *lost authority* rather than as failed work, so
  it is read as a credential to re-provision rather than a task to debug.

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
