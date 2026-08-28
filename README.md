# Barista Apps

**The open userland for [Barista](https://github.com/mpuig/barista.sh).**

Barista Apps defines the vendor-neutral contract that lets an agent app run
unchanged against a local, single-user host or a managed service — including
[Barista Cloud](https://barista.sh) — without depending on either
implementation's private API.

## Status

Implemented and in use. The contracts, the conformance suite, the local
provider, the Python SDK and the reference apps below all exist and are tested;
`openspec/` holds the proposals and ratified specs behind them, and remains the
place to start for *why* something is shaped the way it is.

Conformance, as last measured against a managed provider (Barista Cloud beta,
suite `0.1.0a1`, 2026-08-28, including the explicitly separate real-TTL case):

```
cases: passed=25 failed=0 skipped=0 -> conformant=True
```

certifying `core`, `session.pause_resume` and `grants.delegated`. The other
optional profiles are **registered but not yet certifiable** — the suite has no
deep cases for them, and by its own rule an absent case cannot certify an
advertised profile. See [`conformance/README.md`](conformance/README.md).

## What this is

- **App Manifest** — a versioned, digest-pinned package format: OCI workload,
  commands/endpoints, required/optional host capabilities, least-privilege
  permissions, and state-transfer hooks.
- **Host API** — the provider-neutral wire contract (HTTP/JSON, OpenAPI) for
  session lifecycle, exec/attach/events, artifacts, snapshots, forks, capsules,
  and capability discovery. Apps target this, never a provider's private API.
- **Conformance suite** — black-box tests every provider must pass, including
  a mandatory profile that runs with Barista Cloud unreachable.
- **Local provider** — a useful, single-user reference implementation of the
  Host API over a local `barista.sh` Node Agent. No account, no proprietary
  component.
- **SDK** — provider selection, capability negotiation, idempotency, streaming,
  typed App Run lifecycle/result collection, and source/forge adapters on top of
  the Host API.
- **Reference apps** — Pi, Claude Code, and Codex adapters; Change Agent
  (single-agent repository patch job); Factory (coordinator/worker fan-out);
  Lift (exact and semantic session transfer); Session Story (redacted,
  non-executable knowledge export).

## What this is not

- Not a second hypervisor or runtime — that lives in
  [`barista.sh`](https://github.com/mpuig/barista.sh).
- Not a multi-tenant service, billing API, or marketplace — that is
  Barista Cloud's job, implemented as *one* Host API provider among others.
- Not a place for harness-specific fields in the open contract — agent-specific
  behavior stays in adapters.

## Layout

```text
contracts/
  host-api/          # OpenAPI + streaming schemas
  app-manifest/       # JSON Schema
  session-story/      # JSON Schema / media types
sdks/
  python/
providers/
  local/
conformance/
apps/
  pi/
  claude/
  codex/
  change-agent/
  factory/
  lift/
  story/
```

Each package is independently versioned; this is a monorepo of packages, not
one privileged daemon.

## Relationship to the other Barista repositories

| Repository | Role |
|---|---|
| [`barista.sh`](https://github.com/mpuig/barista.sh) | Open-source execution kernel: Node Agent, runtime trait, snapshots. Supplies the fork/capsule/grant primitives this repository consumes — but never sees apps, tenants, or billing. |
| `barista-apps` (here) | The open contract and userland: App Manifest, Host API, conformance, local provider, SDK, reference apps. |
| Barista Cloud | A proprietary, tenant-aware implementation of the Host API defined here, plus collaboration features (capsule registry, sharing, branch promotion) built on top. |

## Contributing

Read the proposal in `openspec/changes/apps-001-portable-agent-apps/` first.
Requirements use SHALL and must be testable against both the local provider
and a Cloud-shaped provider unless explicitly capability-gated. Every new
change proposal must state how it remains usable with Barista Cloud absent.

## License

Apache-2.0 — see [`LICENSE`](LICENSE).
