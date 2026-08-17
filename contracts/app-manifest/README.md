# contracts/app-manifest

The versioned **Barista App Manifest**: a portable, least-privilege declaration
for packaging and running an app on any conformant Host API provider.

- **Schema:** [`v1alpha1/schema.json`](v1alpha1/schema.json) (JSON Schema
  draft 2020-12)
- **Media type:** `application/vnd.barista.app-manifest.v1alpha1+json`
- **Spec:** `openspec/changes/apps-001-portable-agent-apps/specs/app-manifest/spec.md`

## Identity and digest rules

An app's identity is `name` + `version` + `workload.digest`. The workload MUST
be pinned to an immutable OCI digest (`sha256:…`/`sha512:…`); a mutable tag in
`workload.image` is a human label only and never establishes identity. A
manifest without a digest is rejected before any session is created.

## Capability vocabulary

Capabilities are discoverable Host API profiles an app can require or optionally
use. Required capabilities must be advertised by the provider before any side
effect; the discovered optional set is delivered to the app at launch.

| Capability | Meaning |
|---|---|
| `session.pause_resume` | Pause and resume a session |
| `session.snapshot.exact` | Exact (memory) snapshot |
| `session.fork` | Branch exact state into a new session |
| `capsule.export` | Export a portable capsule |
| `capsule.import` | Import a portable capsule |
| `grants.delegated` | Mint scoped delegated grants |
| `story.publish` | Publish a redacted Session Story |
| `branch.evaluation` | Fork fan-out with result receipts |

## Permission vocabulary (actions)

Least-privilege Host API actions the app may perform:

`session.create`, `session.get`, `session.list`, `session.delete`,
`session.exec`, `session.attach`, `session.events`, `session.pause`,
`session.resume`, `session.fork`, `artifact.read`, `artifact.write`,
`capsule.export`, `capsule.import`, `story.publish`.

A host may grant less than requested only when the app declared a valid
`degraded_modes` entry describing which capabilities it can run without.

## Secrets

`permissions.secrets` entries carry a `name` and a provider-resolvable `ref`
only. Plaintext secret values are rejected by the schema (the entry object is
closed). Harness config, transcript formats, and model identifiers live under
namespaced `metadata` or artifacts and stay opaque to the host.

## Examples

- [`v1alpha1/examples/factory.json`](v1alpha1/examples/factory.json) — full
  manifest (capabilities, permissions, degraded mode, state transfer).
- [`v1alpha1/examples/minimal.json`](v1alpha1/examples/minimal.json) — smallest
  valid manifest.
- `v1alpha1/invalid/` — fixtures that MUST fail validation (missing digest,
  plaintext secret, unknown capability). Used by the golden tests.
