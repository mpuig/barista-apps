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
- **A scope is required to widen one.** An action is written either as a bare id
  — which means the app's own session and nothing else — or as
  `{ "action": …, "scope": "created_sessions" }`. There is no way to reach the
  sessions an app creates without saying so at the point of declaration.

## Creating child sessions

An app that fans work out declares both halves: how many children, and what they
get.

```json
"child_sessions": {
  "max_concurrent": 16,
  "max_total": 256,
  "allow_descendants": false,
  "actions": [
    { "action": "session.exec",   "scope": "own_session" },
    { "action": "artifact.write", "scope": "own_session" }
  ]
}
```

The **provider is the only minter**: it mints the child's grant from this list
at child-session create. A coordinator asks for a child session and never
handles that credential. `allow_descendants` defaults to *false*, so a child
cannot fan out further unless the manifest says it may.

> **The schema does not check that a child's actions are a subset of the app's
> own.** JSON Schema cannot relate the two lists, so a manifest that
> over-delegates validates cleanly and is refused by the *provider* at install.
> Run the rules yourself before you ship one:
>
> ```bash
> python3 contracts/app-manifest/v1alpha1/rules.py apps/<name>/manifest.json
> ```
>
> See [the contract README](../contracts/app-manifest/README.md#the-subset-rule--and-what-the-schema-does-not-enforce).

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
by `scripts/supply_chain_check.py` — schema, digest pinning, reference-only
secrets, **and** the semantic rules the schema cannot carry.
