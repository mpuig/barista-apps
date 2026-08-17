## Context

See proposal.md — Why. Barista has a privileged protobuf Node Agent contract;
Cloud has a tenant REST/WebSocket API; the Factory demo and ACP bridge currently
live in the private repository. Neither API is the right app portability seam.
The kernel change supplies neutral fork/capsule/grant mechanisms and Cloud will
implement the open contract defined here.

## Goals / Non-Goals

**Goals:**
- Let an app run against local, Cloud, or third-party providers with endpoint
  configuration and capability negotiation only.
- Make standalone OSS genuinely useful and conformance-testable with Cloud
  unavailable.
- Keep harnesses replaceable and publish reference Pi, Claude, Codex, Factory,
  Lift, and Story packages.

**Non-Goals:**
- A second hypervisor/runtime implementation, a multi-tenant local host, a
  billing API, a global marketplace, or provider-specific placement controls.
- Normalizing every agent transcript into one lossy universal schema.
- One Git repository per app in the first release; package boundaries suffice.

## Decisions

### D1 — One OSS monorepo with independently versioned packages

Use this layout:

```text
contracts/
  host-api/          # OpenAPI + streaming schemas
  app-manifest/      # JSON Schema
  session-story/     # JSON Schema/media types
sdks/
  python/
providers/
  local/
conformance/
apps/
  pi/
  claude/
  codex/
  factory/
  lift/
  story/
```

Separate repositories would isolate release cadence but multiply CI, security
policy, cross-version testing, and changes spanning SDK plus apps before APIs
stabilize. The monorepo keeps clear package/image boundaries and allows a later
split without changing wire contracts.

### D2 — Host API is HTTP/JSON with explicit streaming transports

The canonical contract is OpenAPI for request/response operations plus versioned
schemas for SSE event cursors and WebSocket attach frames. This matches browser,
CLI, local-daemon, and Cloud constraints and is language-neutral. SDKs are
generated/thin wrappers. Apps never call Contract A; the local provider maps Host
API to it and Cloud maps Host API through its gateway.

Making the Node protobuf public to apps is simpler but exposes a privileged,
node-scoped API, lacks tenant-safe handles, and makes browser clients awkward.

### D3 — Capabilities form profiles, not provider names

Core includes discovery, sessions, exec/attach/events, lifecycle, and artifacts.
Optional profiles include exact snapshot, fork, capsule, delegated grants,
story publication, and evaluation. Apps branch only on these identifiers and
limits. No `if cloud`/`if local` exists in the portable SDK.

### D4 — Manifest is declarative; controller code remains unprivileged

An app is a signed/digest-pinned manifest plus OCI workloads and optional client
or coordinator package. It declares capabilities and least-privilege actions.
Controllers run as ordinary processes/sessions holding delegated Host API grants,
never as plugins loaded into the Node Agent or provider gateway. This preserves
kernel/provider trust boundaries even for self-generated apps.

### D5 — Local provider is a reference, not a hidden Cloud clone

The local provider implements the core Host API over one user's Node Agent,
SQLite/local files, and optional S3-compatible capsule storage. It binds to a
user-owned socket by default. It deliberately omits tenants, billing, global
placement, public URLs, organization policy, and collaboration. Those omissions
are absent capabilities, not crippled code paths.

### D6 — Adapters own semantic state; exact state belongs to capsules

The adapter interface exports opaque native attachments plus a common inventory
of workspace, VCS, transcript, skills, tool config, environment, and continuation
prompt. Pi, Claude, and Codex packages translate their own formats. Providers
store bytes and media types without understanding them. Exact Lift uses kernel
capsules; semantic Lift uses adapter bundles and must report fidelity.

### D7 — Stories are safe views, never executable exports

The Story app selects records, invokes deterministic redaction, emits canonical
content-addressed bundles, and optionally signs them. It cannot include capsule
objects or grants. Providers may host/discover stories, but share URLs and social
features are extensions above the open bundle format.

### D8 — Conformance is the portability product

Tests launch real apps against a black-box endpoint. The mandatory standalone
job blocks Cloud DNS and credentials. Cloud and third parties run the same suite;
profile badges are derived from results, not marketing declarations. Golden
fixtures pin canonical manifests, errors, event replay, and story redaction.

## Risks / Trade-offs

- [Umbrella first change is large] → Implement in vertical order: contracts and
  conformance skeleton, local core, SDK, one Pi adapter, Factory, then optional
  fork/Lift/Story profiles. Each milestone is independently testable.
- [Open Host API competes with Cloud's existing API] → Share domain semantics,
  keep the open API narrow, and treat Cloud routes as a provider/superset.
- [Adapter formats change quickly] → Version adapters and preserve native state
  opaquely; semantic fidelity reports fail loudly.
- [Local provider becomes insecure remote server] → Loopback/user socket default,
  explicit remote-auth opt-in, and no multi-tenant claims.
- [Manifest permissions appear enforceable where a provider lacks enforcement]
  → Capability-gate permissions and fail installation when required enforcement
  is absent.

## Migration Plan

1. Publish Host API, App Manifest, Story schemas, media types, and conformance
   fixtures as `v1alpha1`.
2. Implement local provider core and prove the mandatory Cloud-absent profile.
3. Publish the first SDK and Pi adapter; use them as the contract's reference
   consumer.
4. Port Factory from the private demo without copying Cloud credentials or
   internal endpoints; validate harvest-before-reap locally and in Cloud.
5. Add Claude and Codex adapters.
6. Enable exact fork/Lift after the pinned kernel exposes those profiles; keep
   semantic Lift available independently.
7. Add Story generation/redaction; Cloud may add hosting only after the open
   bundle passes standalone conformance.

## Open Questions

- Which SDK follows Python first. The contract and app packaging are language
  neutral; a TypeScript SDK can be generated once the alpha schema settles.

