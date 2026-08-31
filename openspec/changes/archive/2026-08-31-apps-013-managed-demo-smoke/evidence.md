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

## Published model workloads

Reviewed source revision `9fd30d2f4f50a6d7378f29ee0d2979c4a9a554d7` produced three
public OCI indexes. Anonymous registry reads reported the index and native
manifest identities below; a local image id was not accepted as publication
evidence.

| App | OCI index | linux/amd64 manifest | linux/arm64 manifest |
|---|---|---|---|
| Claude 2.1.251 | `sha256:7088e81617f0243f8718449240d12439261c588904ddf8ecff1603e8fc929328` | `sha256:d26ac2afde3583a10d2d0047410524a95a8182c397dd64660d2ae4f2d91225b4` | `sha256:dbbca01d2b2d71adcc00b3abdc81c2b777dc7536d99e38604fe637aaf06a1af1` |
| Pi 0.73.1 | `sha256:867af9900c3bc0c44662de5b84d474684f18b0355156bd21cc1aff00f32814e4` | `sha256:5ddb96449d32ad5e8734e779efd9da10d136e015acb1e26edb32deb683e369ab` | `sha256:51a21d27fec236d210f856f03681a5a93963b0d51eba13ccb8b9893c5e361ac9` |
| Codex 0.151.0 | `sha256:5157e3dcbaafc11876b51a5638a65dc23f396e4234502f0f8d105d3767e16726` | `sha256:91a3f0172a341929ccf5af764e6c464dd498935da14ff4080ea03447dc6dd364` | `sha256:bc21ee435a6688ba0be66b5da93cdba6fb58f5766fede13e5ee93600fb92d823` |

Both architecture configs identify the non-root `node` user, the fixed
`/usr/bin/sleep infinity` entrypoint, and the reviewed source revision. Native
amd64 pulls on the Hetzner node verified the exact CLI versions, and native
arm64 execution verified them on the acceptance workstation. CA bundles were
present. A generated non-production sentinel scan passed for all three images;
no operator credential was used as scanner input.

## Managed model-profile replay

Cloud PR [#215](https://github.com/mpuig/barista-cloud/pull/215) added bounded,
generic operator resolution for only the references declared by an installed
manifest. Signed appliance sequence 5 deployed that reviewed implementation.
The `managed-release-acceptance` tenant then installed the three immutable
manifests. Anthropic/OpenAI API keys and Codex's EU endpoint remained
provider-side bindings; the runner process removed model credential variables
from its environment and supplied no credential value in configuration or
argv.

Two failed runs remain bounded diagnostic evidence:

- `smoke-b536597f13c44704bdfafddea80f330b`: Claude and Pi passed, while Codex
  failed readiness during its first background image pull. Cleanup deleted all
  created sessions.
- `smoke-fcffaeb252674f258a35d5f9a0e815bc`: Claude and Pi passed, while Codex
  produced no marker because Codex 0.151.0 requires API-key login state rather
  than consuming the injected environment variable directly. Cleanup again
  deleted all created sessions.

Apps PR [#68](https://github.com/mpuig/barista-apps/pull/68) reviewed the
correct non-interactive boundary: the provider-injected key moves to
`codex login --with-api-key` over stdin with login output suppressed, and the
provider-resolved EU endpoint moves into Codex's bounded configuration option.
Neither resolved value enters the configured argv, report, manifest, or image.

Report `smoke-dd9c8170ee8b4d87b0a189c848b76744` passed from
`2026-08-31T08:49:04Z` through `08:50:19Z`:

| Step | Result | Evidence |
|---|---|---|
| Managed lifecycle | passed | readiness, exec, pause/resume, filesystem continuity, deletion |
| Factory dependency mission | passed | fresh producer/consumer dependency transfer and independent checks |
| Public URLs | passed | Cloud, Factory controller, and Program 21 returned HTTP 200 |
| Claude 2.1.251 | passed | `CLAUDE_SMOKE_OK`, operation `op-01M1BG5B1NNQ1SVQWV27EW667F`, pause/resume |
| Pi 0.73.1 | passed | `PI_SMOKE_OK`, operation `op-01M1BG5SMWJNAPFNPEW78AY84P`, pause/resume |
| Codex 0.151.0 | passed | `CODEX_SMOKE_OK`, operation `op-01M1BG646QN5CEHP4DPE4DYES3`, pause/resume, EU endpoint |

The report is retained at `/tmp/barista-managed-model-smoke-green.json` on the
acceptance workstation. Its bounded content was scanned against the tenant
token and both provider credentials with no match. The tenant session inventory
was empty after the run.
