## Why

The Barista kernel can run standalone and Barista Cloud can host sessions, but
there is no open userland contract that lets one app run unchanged on both—or
on a third-party implementation. A portable app specification, conformance
suite, local provider, and useful reference apps turn Barista from an engine
into an ecosystem without making Cloud a hidden dependency.

## What Changes

- Define the versioned Barista App Manifest: digest-pinned OCI workload,
  commands and endpoints, required/optional host capabilities, least-privilege
  permissions, state transfer hooks, and opaque app metadata.
- Define the provider-neutral Barista Host API for ensure, lifecycle, exec,
  attach, events, snapshots, forks, capsules, artifacts, grants, publication,
  and capability discovery.
- Ship a conformance suite and a single-user local provider that maps the Host
  API to a loopback Barista Node Agent, local storage, and local credentials;
  it must require no Barista Cloud account or proprietary component.
- Define an SDK boundary and reference adapters for Pi, Claude Code, and Codex
  without putting harness-specific fields in the Host API.
- Move/evolve Factory into an OSS app that coordinates workers only through the
  Host API and harvests artifacts before successful workers are reaped.
- Define Lift with exact and semantic transfer modes, and Session Story as a
  deterministic, redacted, non-executable knowledge bundle.
- Organize the repository as a monorepo of independent packages/apps rather
  than one privileged daemon with every workflow compiled into it.

## Capabilities

### New Capabilities

- `app-manifest`: portable packaging, capability, permission, endpoint, and
  lifecycle declaration for a Barista app.
- `host-api`: canonical provider-neutral session/application wire contract and
  error model.
- `provider-conformance`: black-box tests every provider must pass, including a
  mandatory Cloud-absent profile.
- `local-host-provider`: useful single-user implementation over `barista.sh`
  with no proprietary dependency.
- `app-sdk`: provider selection, capability negotiation, idempotency,
  wait/stream helpers, and adapter interfaces.
- `agent-adapters`: harness-neutral adapter contract plus Pi, Claude Code, and
  Codex reference packages.
- `factory-app`: durable coordinator/worker fan-out, artifact harvest,
  forensics, notifications, and cleanup as an ordinary portable app.
- `lift-app`: exact transfer for compatible Barista-managed sessions and
  semantic continuation for native or incompatible local agents.
- `session-story`: deterministic redaction and publication of knowledge without
  granting access to executable state.

### Modified Capabilities

_None; this is the first change in the repository._

## Impact

- **New repository layout:** `contracts/`, `sdks/`, `providers/local/`,
  `conformance/`, and `apps/{pi,claude,codex,factory,lift,story}/`.
- **Dependencies:** consumes released, pinned `barista.sh` contracts; Cloud is
  only another provider endpoint and never a build or test dependency for the
  standalone profile.
- **Distribution:** schemas and SDKs are versioned packages; apps are
  digest-pinned OCI artifacts plus signed manifests.
- **Security:** manifests contain secret references, never plaintext; executable
  capsules remain private by default; stories are redacted and non-executable.
- **Compatibility:** apps declare requirements and providers report
  capabilities. Optional functionality degrades explicitly; required
  functionality fails before creating a session.

