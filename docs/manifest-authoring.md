# Authoring an App Manifest

An app is a signed, digest-pinned [App Manifest](../contracts/app-manifest/v1alpha1/schema.json)
plus an OCI workload and an optional controller/adapter package. The manifest is
declarative; controllers run as ordinary unprivileged sessions holding delegated
Host API grants.

## Minimum

```json
{
  "schema_version": "v1alpha1",
  "name": "pi",
  "version": "0.1.0",
  "workload": {
    "image": "ghcr.io/you/pi:0.1.0",
    "digest": "sha256:<64 hex>",
    "architectures": ["aarch64"],
    "entrypoint": ["/usr/local/bin/pi"],
    "readiness": { "type": "none" }
  }
}
```

## Rules that the schema enforces

- **Digest is identity.** `workload.digest` is required and must be an immutable
  `sha256:`/`sha512:` digest. A mutable tag in `workload.image` is a human label
  only. A manifest without a digest is rejected before any session is created.
- **Secrets are references, never values.** `permissions.secrets` entries carry
  a `name` and a provider-resolvable `ref` (e.g. `secret://…`). A plaintext
  value is rejected by the schema.
- **Least privilege.** Declare only the Host API `actions` the app needs. A host
  may grant less than requested only if the app declares a valid `degraded_modes`
  entry.

## Capabilities

Declare required vs. optional host capabilities. A provider **rejects
installation before any side effect** when a required capability is unmet; the
app receives the discovered optional set at launch.

```json
"capabilities": {
  "required": [{ "capability": "session.fork" }],
  "optional": [{ "capability": "capsule.export" }]
}
```

## Harness detail stays opaque

Harness configuration, transcript formats, and model identifiers live under
namespaced `metadata` or in artifacts — never as Host API fields. A new adapter
adds metadata under its own namespace without any provider schema change.

## Validate

```bash
cd contracts/tests && uv run pytest -q     # includes manifest golden tests
```

Every first-party app manifest (`apps/*/manifest.json`) is validated in CI and
by `scripts/supply_chain_check.py` (digest pinning + reference-only secrets).
