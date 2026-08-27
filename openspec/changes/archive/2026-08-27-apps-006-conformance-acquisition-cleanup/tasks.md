# Tasks

## 1. Reproduce the leak

- [x] 1.1 Add a provider double that raises after the probe coordinator is created but before child acquisition completes.
- [x] 1.2 Assert the run reports the acquisition failure and leaves no suite-owned probe session.
- [x] 1.3 Assert operator-supplied probe sessions are still retained.

## 2. Keep partial acquisition reachable

- [x] 2.1 Create the suite-owned cleanup ledger before delegated acquisition starts.
- [x] 2.2 Pass the same ledger through acquisition and append each successful session immediately.
- [x] 2.3 Preserve the ledger when acquisition raises and release it through the runner's existing cleanup path.

## 3. Verify

- [x] 3.1 Mutation: remove the pre-acquisition ledger assignment and confirm the partial-acquisition cleanup test fails.
- [x] 3.2 `( cd conformance && uv run --extra test pytest -q )` green — 22 passed.
- [x] 3.3 `openspec validate --all --strict` green — 15 passed.

Mutation evidence: replacing the pending ledger with assignment only after
`_acquire_delegated` returned failed
`test_partial_credential_acquisition_is_cleaned_up_when_a_request_raises` at
"a partial delegated probe survived the run". The source was restored from a
backup and the full package reran green.
