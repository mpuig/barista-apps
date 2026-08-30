# Evidence

## Offline verification

- Controller integration: `uv run pytest -q` — 67 passed.
- Factory: `uv run pytest -q` — 84 passed; contracts — 74 passed; standalone acceptance — passed; supply-chain check — passed.
- The generated product service manifest was independently validated against the App Manifest v1alpha1 schema.
- Targeted Ruff checks passed for controller source, projection/deployment code, provisioning, and tests.
- Generic mapping tests cover chronological evidence, exact commits and digests, disabled and enabled deployment seams, monotonic corrective revisions, immutable event identities, fixed-adapter I/O, durable request resolution, authority separation, and projection failure isolation.
- `openspec validate apps-012-generic-activity-projection --strict` — valid.

## Security mutation

Removed the accepted-program predicate before source-side deployment and ran:

```text
uv run pytest -q tests/test_activity_projection.py -k cannot_deploy_unaccepted
FAILED (mutation_exit=1)
```

The original predicate was restored. With the unmodified implementation the test proves that a Cloud action request cannot turn an `implementing` program into deployable work or invoke the trusted deployment adapter.

## Managed evidence

Pending Cloud source registration, controller projection of the persisted successful program, authenticated UI verification, explicit human Deploy request, and verified source-side settlement.
