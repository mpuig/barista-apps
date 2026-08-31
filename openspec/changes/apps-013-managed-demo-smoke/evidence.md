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

## Green release-gate replay

After Cloud sequence 4 became active, an operator created a distinct
`managed-release-acceptance` tenant with the enterprise capability set and a
mode-0600 one-time API-key file. The normal appliance-acceptance tenant and its
plan were not changed. Discovery advertised exactly `grants.delegated` and
`session.pause_resume`.

Report `smoke-f580adf1d65b4e2894e4ba442a1ca9b3` then passed:

- managed lifecycle and filesystem continuity: 11.71 seconds;
- dependency-gated Factory mission: 27.27 seconds;
- Cloud health: HTTP 200;
- GitHub Factory controller health: HTTP 200; and
- Program 21 generated application: HTTP 200.

All test-created successful sessions were deleted. The report is retained at
`/tmp/barista-managed-smoke-green.json` on the acceptance workstation and its
run id is recorded in managed deployment provenance.

## Outstanding managed evidence

The model profile still requires immutable published Claude, Pi, and Codex
images installed with provider-side secret references. That prerequisite was
not weakened or bypassed.
