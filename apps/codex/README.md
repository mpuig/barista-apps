# apps/codex

The **codex** harness as a portable Barista app: an [App Manifest](manifest.json)
plus a semantic-transfer adapter that runs against any conformant Host API
provider.

- **Adapter:** `barista_app_codex` — implements the SDK `Adapter` interface
  (detect, capabilities, export_semantic, continuation_launch, collect_result).
- **Manifest:** digest-pinned OCI workload, supported native-version
  declaration, and model-provider credential **references** (never plaintext).
- **Spec:** `openspec/changes/apps-001-portable-agent-apps/specs/agent-adapters/spec.md`

## What the adapter does

- **Detect** the harness's native session state for a workspace and report the
  native version.
- **Export** a semantic bundle that preserves the native transcript as an
  **opaque** attachment (bytes + media type) and carries an honest
  `FidelityReport` — semantic continuation, never a claim of exact memory
  transfer.
- **Refuse loudly** (`AdapterCompatibilityError`) when the native version is
  unsupported, rather than exporting a lossy bundle silently.
- **Continuation** builds the command to resume the harness from the exported
  state.

No adapter puts harness-specific fields into the Host API or the manifest
envelope; harness detail lives in the adapter and in namespaced metadata.

## Workload image

`Dockerfile` packages Codex CLI 0.151.0 on a digest-pinned
multi-architecture Node base. It includes CA certificates, Git, and ripgrep,
runs as the non-root `node` user, and keeps the session alive with a fixed inert
entrypoint. Codex invocations run through Host API `Exec`; model credentials
come only from the manifest's provider-resolved secret reference. Codex 0.151.0
requires API-key login state, so managed non-interactive invocation pipes the
provider-injected key to `codex login --with-api-key` over stdin before exec;
the key never enters argv. Regional API routing is also provider-resolved. This
CLI release receives it through the bounded `openai_base_url` invocation option
because it does not consume `OPENAI_BASE_URL` directly. Neither value is baked
into the image or stored in the manifest.

Build from the repository root and always publish both supported platforms:

```sh
docker buildx build -f apps/codex/Dockerfile \
  --platform linux/amd64,linux/arm64 <publish arguments> .
```

## Tests

```bash
cd apps/codex && uv run --extra test pytest -q
```

Fixture-based round trips prove native bytes are preserved verbatim, the bundle
validates against the semantic-state contract schema (no extra fields), and an
unsupported native version is refused.
