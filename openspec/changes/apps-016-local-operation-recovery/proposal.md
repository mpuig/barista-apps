## Why

The local provider must create valid node identities and recover retries without duplicate workloads or stale lifecycle replays.

## What Changes

- Repair operation identity and retry ownership at the implementation boundary.
- Add deterministic regression and mutation checks.

## Capabilities

### New Capabilities
- `local-operation-recovery`: reliable ownership across retries and delayed completion.

### Modified Capabilities

None.

## Impact

Provider implementation and offline tests; no public wire changes. Usable with Barista Cloud absent.
