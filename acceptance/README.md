# Acceptance

This package keeps evidence classes separate:

- `tests/test_standalone_acceptance.py` runs offline with Cloud blocked;
- `tests/test_managed_acceptance.py` contains opt-in public Host API cases;
- `barista-managed-smoke` selects managed cases as a bounded release gate;
- pytest's `slow` marker preserves real elapsed-time grant-renewal evidence.

See [Managed demo smoke gate](../docs/managed-acceptance.md) for configuration,
profiles, report semantics, and credential handling.
