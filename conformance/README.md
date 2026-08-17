# conformance

Black-box conformance suite for **Barista Host API** providers. It proves a
provider preserves portable app semantics and advertises only behavior it
actually supports — through the published contract alone, with no private hooks.

- **OpenSpec:** `openspec/changes/apps-001-portable-agent-apps/specs/provider-conformance/spec.md`

## Run it against a provider

```bash
cd conformance
uv run barista-conformance \
  --endpoint http://localhost:8088 \
  --provider-name local --provider-version 0.1.0 \
  --report report.json
```

Or from the environment:

```bash
BARISTA_HOST_API_ENDPOINT=http://localhost:8088 \
BARISTA_CONFORMANCE_STANDALONE=1 \
uv run barista-conformance
```

The process exits non-zero when the provider is **not** conformant, so CI can
gate on it. The JSON report records contract version, suite version, provider
identity, advertised profiles, per-case status, and violations.

## The rules it enforces

- **Core profile must fully pass** — discovery, manifest rejection,
  ensure/get/idempotency, exec + readable operations, resumable event cursors,
  content-addressed artifacts, classified errors, no provider-internal leakage,
  cleanup, and *unsupported profiles return a capability error rather than a
  fake success*.
- **A skip never certifies an advertised profile.** If a provider advertises an
  optional profile, that profile must have at least one case and all its cases
  must pass. Advertising a profile this suite cannot demonstrate is a violation.
- **Standalone (Cloud-absent) is enforced, not documented.** With
  `--standalone`, a process-wide audit-hook guard fails the run if anything
  resolves Barista Cloud DNS, connects to a Cloud address, or imports a
  proprietary module.

## Profiles

- `core` — mandatory.
- `session.pause_resume` — implemented.
- `session.snapshot.exact`, `session.fork`, `capsule.export`, `capsule.import`,
  `grants.delegated`, `story.publish`, `branch.evaluation` — registered
  profile identifiers; deep cases land as the corresponding Host API endpoints
  are defined (they depend on the kernel fork/capsule work and later apps
  milestones). Until then this suite version honestly **cannot certify** a
  provider that advertises them.

## Self-tests

The suite ships an in-process mock provider (`tests/mock_provider.py`) — a test
double, *not* the real local provider — so its own logic is verified offline:

```bash
cd conformance && uv run --extra test pytest -q
```
