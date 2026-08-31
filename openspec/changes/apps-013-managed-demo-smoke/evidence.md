# Evidence: Managed demo smoke gate

## Automated tests

- Acceptance package: 7 passed; managed infrastructure cases skipped only in the
  ordinary offline pytest invocation; the real slow case remained deselected.
- Cloud-blocked standalone acceptance: passed.
- Ruff over the runner and managed acceptance tests: passed.
- Supply-chain check: passed.
- Strict OpenSpec validation: passed.

## Managed beta execution

The first implementation run used the separate appliance-acceptance credential,
reviewed loopback Factory image identities, and three named public checks. The
lifecycle step passed in 10.16 seconds. Cloud health, the GitHub Factory
controller, and Program 21 each returned HTTP 200. The Factory pytest case
skipped because this tenant advertises only `session.pause_resume`, not
`grants.delegated`.

That first runner incorrectly treated pytest's zero exit for one skipped test as
a passed step. This was a genuine gate defect. The command now requests skip
reasons and rejects any selected skip. A second managed run recorded:

- lifecycle: passed in 10.26 seconds;
- Factory dependency mission: failed because the required capability was not
  advertised;
- overall state: failed and process exit: nonzero.

The report is retained at
`/tmp/barista-managed-smoke-after-skip-fix.json` on the acceptance workstation.
No credential value appears in either report. A separate earlier managed
BAR-081 replay remains successful Factory evidence, but was not substituted for
the skipped current check.

## Mutation evidence

| Mutation | Named test | Result |
|---|---|---|
| Disable managed-skip refusal | `test_pytest_step_refuses_a_skipped_managed_check` | failed as required |

## Outstanding managed evidence

A complete green default report requires an ordinary credential for an
enterprise/acceptance tenant advertising `grants.delegated`. The model profile
requires immutable published Claude, Pi, and Codex images installed with
provider-side secret references. Neither prerequisite was weakened or bypassed.
