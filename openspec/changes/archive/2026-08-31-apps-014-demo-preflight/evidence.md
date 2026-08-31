# apps-014 evidence

## Reviewed implementation

- Implementation PR: <https://github.com/mpuig/barista-apps/pull/71>
- Version-stream correction: <https://github.com/mpuig/barista-apps/pull/72>
- Reviewed source: `4835f5d094360431f07eb6242d8f1a9d7b04484f`

The first managed run, `smoke-5c981dda3bb74f74b7228debca88330e`,
correctly passed the default gate and Claude warm-up, then refused Pi because
Pi writes its version on stderr while the marker gate intentionally inspects
bounded stdout. Its created sessions were deleted. PR #72 made the reviewed
probes merge CLI stderr into bounded stdout and added a regression test over
the published configuration before another managed run.

## Validation

Before merge:

- Acceptance: 10 passed, four expected offline managed skips, one slow deselection.
- Contracts: 74 passed.
- Supply-chain check passed.
- OpenSpec: 16/16 strict.
- Ruff and `git diff --check` passed.
- All 15 required GitHub CI jobs passed on both implementation PRs.

## Managed no-spend rehearsal

Green run `smoke-4806d1ddb07f4e1095c2495ea5ca44f4` used the
`preflight` profile against managed beta. In 68 seconds it proved:

- managed create, exec, filesystem continuity, pause/resume, and delete;
- a fresh dependency-gated Factory mission;
- HTTP 200 for Cloud, the Factory controller, and Program 21;
- provider-side binding presence and pinned versions for Claude 2.1.251, Pi
  0.73.1, and Codex 0.151.0;
- the provider-resolved Codex base URL matched the reviewed EU endpoint;
- pause/resume for all three warm-up sessions;
- unconditional deletion and zero remaining tenant sessions.

The operator-binding bundle remained on the control plane. A root-side scan
received the report through SSH stdin, checked all three non-empty binding
values locally, and found zero matches. A separate scan found no managed tenant
token match. No model inference argv ran, so this is explicitly warm-up—not
paid-model—evidence.

Report: `/tmp/barista-managed-demo-preflight-green.json`.
