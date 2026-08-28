# apps-007 acceptance evidence

Recorded 2026-08-28 from the repository-backed Factory acceptance fixture. This
is offline `FakeForge` evidence, not a claim that a public GitHub pull request was
created or that managed acceptance has run.

## Command

```sh
cd apps/factory
uv run --extra test pytest tests/test_software_change.py -q
# 6 passed
```

The complete affected-package run also passed:

- SDK: `66 passed`
- Factory: `62 passed`
- contracts: `72 passed`
- conformance self-tests: `22 passed`
- local provider: `14 passed`
- Story / Lift: `10 passed` each
- Pi / Claude / Codex: `5 passed` each
- Change Agent: `5 passed`
- standalone acceptance: `1 passed, 3 skipped, 1 deselected`
- `python3 scripts/supply_chain_check.py`: `OK`
- `openspec validate --all --strict`: `12 passed, 0 failed`

## Factory software-change receipt

| Field | Observed value |
|---|---|
| Run | `factory-change` |
| App | `factory@0.1.0` |
| Declared workload | `sha256:5f0f2b8c1d3e4a5b6c7d8e9f00112233445566778899aabbccddeeff00112233` |
| Resolved project commit | `56c3318137582aa1534d67af233d984c42efb5a6` |
| Integration check | `sha256:e07997aeb0140e1372520e161dff4b2c59b59ca0a4c0b18d3670ea11a4aacb40` |
| Worker receipt A | `sha256:53b41c2992621a13d5eef04539085dc3f31b1d25a966c531387ee0657adbae44` |
| Worker receipt B | `sha256:274810c847d418fe93d36759a826bdfcabc8c906b7a5dbee07b8da0583a95a36` |
| Integrated patch | `sha256:8bdd27cc6832e634af10ed21cf491b24aecff75fd339ee68f43a0173a424dd0d` |
| Canonical result | `sha256:0a700e6f2ee62086d3f83205845a47ff6c7a1a57c52dc2ae821a45bd39bf941e` |
| Fake draft | `fake://pull/1` |
| Fake draft head | `1468c39813edd42b9003317b81b912765347602e` |

Both workers started from the recorded base in isolated workspaces. Worker A
changed the implementation **and replaced the acceptance file with `pass`**.
The coordinator reasserted its own acceptance file, verified the integrated A+B
tree, and only then invoked the explicit draft delivery. The test confirmed:

- the weakened worker criterion was absent from the integrated result;
- the check subprocess did not inherit `BARISTA_HOST_API_TOKEN`;
- every patch was copied and registered on the owner before its successful
  worker was reaped;
- the fake pull was a draft with exact base/head and patch identity;
- replay returned the same terminal result and did not create workers or a
  second pull request;
- failed check and failed worker cases created no pull request and retained
  recoverable evidence.

## Managed-provider evidence

The same repository-job and coordinating-operation shapes were then exercised
through the managed Host API. The only provider configuration was the beta
endpoint and its saved API credential; app-specific input remained the opaque
`BARISTA_APP_RUN` document. Temporary sessions and all child sessions were
removed after result collection; `barista ls` returned only the pre-existing
`counter` session.

### Factory

- beta-local image: `127.0.0.1:5000/barista-factory:0.5.3`
- workload digest: `sha256:654e49cd51095c364b6793d8e7ed248800782fb59935189813c059c5442cd303`
- run: `factory-repo-1787918409`
- public project base: `7fd1a60b01f91b314f59955a4e4d4e80d8edf11d`
- issue revision: `sha256:90caa2f45a6f87927f01d0ea2d8b22538acbefec61791072631651805374e062`
- worker receipts: `sha256:917e1026bcfb1ec5017072a6282db269dbb5d47564342f4177dfec8306015d88`, `sha256:1d886888027bb03a436f5dfeea47ee0714bc0b6e3b54f3eeaadcde142fb558ed`
- integration check: `sha256:517886379a40ec129c0cc58985aa21b30772401b8601304ac4a1e0c8de41404f`
- integrated patch: `sha256:fc02a3244ad5cf73e98980d55bc303926650c306f67907f76f203d12cfae9ac6`
- canonical result: `sha256:4d8f8bad8849dddfdc642e0385983acc425004747dd39e1d9c32a1e710b49ead`
- outcome: `succeeded`; six artifacts registered; two successful workers reaped

### Single-agent job

- beta-local image: `127.0.0.1:5000/barista-change-agent:0.2.1`
- workload digest: `sha256:e6e667bb9271d555c3733d7057240238875cd6d387cf7244f5b199e45dfb7224`
- run: `change-agent-repo-1787918530`
- public project base: `7fd1a60b01f91b314f59955a4e4d4e80d8edf11d`
- change check: `sha256:c138e646577e64d2795a26faae1cd9d763068b5768f98067979331f9af6c5a10`
- patch: `sha256:a1cf03845fcda560dcf55ac6828a968163155a23a800b73d8e231bec6fb3ffe1`
- canonical result: `sha256:0b68809a3e48af934e167dc9b3add3aa4b1dcd67ab276ed3d10e560d21716099`
- outcome: `succeeded`; canonical result was collected before cleanup

This managed pass exposed and fixed two portability defects before the evidence
above was accepted: the single-agent manifest had not declared its owning grant,
and terminal PID 1 exited before a runner could exec-read result bytes. Both
terminal apps now declare the needed grant and keep a bounded 120-second result
handoff window (cleanup may end it earlier).

Managed draft publication was deliberately not requested against a third-party
fixture repository. Explicit publication and idempotency are proven with
`FakeForge`; `GitHubForge` has offline HTTP/Git tests for exact-head push, draft
creation, patch markers, moving-base checks, and token-safe transport.

## Single-agent and standalone evidence

```sh
cd acceptance
uv run pytest -q
# 1 passed, 3 skipped, 1 deselected
```

That acceptance launches the `change-agent` repository job against local Git,
checks its canonical result, and exercises explicit draft delivery through the
offline forge while Cloud DNS, managed credentials, and proprietary imports are
blocked. Managed-provider parity remains task 6.2 and is intentionally not
inferred from this offline record.
