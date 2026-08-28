# Validation evidence

Date: 2026-08-28

## Offline implementation evidence

- GitHub Factory controller: `19 passed`
- Deterministic GitHub issue worker: `5 passed`
- Factory: `63 passed`
- Python SDK: `68 passed`
- Contracts: `73 passed`
- Conformance self-tests: `22 passed`
- Local provider: `14 passed`
- Standalone acceptance with Cloud blocked: `1 passed, 3 skipped, 1 deselected`
- Supply-chain check: `supply-chain check OK`
- Strict active OpenSpec validation: all `3` active changes valid
- Ruff for the two new packages: `All checks passed!`

The offline fake-GitHub/fake-Host acceptance covers signed webhook ingestion,
trusted run projection, canonical succeeded Factory result, independently
size/digest-verified patch retrieval, exact repository/objective identities,
idempotent draft delivery, issue comment, durable result, and owning-session
cleanup. Dishonest cases cover invalid signatures, wrong repository, unsupported
events/actions, malicious issue text, delivery/issue retries, failed Factory
checks, wrong patch digest, moving base, and failure preservation.

## Packaging evidence

Both new Dockerfiles built successfully on the local Docker engine:

- `apps/github-issue-worker/Dockerfile`
- `integrations/github-factory-demo/Dockerfile`

The installed worker entrypoint produced the expected bounded configuration
error when objective variables were absent. The installed controller CLI exposed
`serve`, `setup`, `teardown`, `status`, and `accept`. Temporary local image tags
were removed after validation.

## Real GitHub acceptance

The opt-in acceptance succeeded against the deployed signed-webhook controller
and disposable public repository:

- Controller: `https://github-factory.beta.barista.sh`
- Repository: `https://github.com/mpuig/barista-factory-demo`
- Issue: `https://github.com/mpuig/barista-factory-demo/issues/2`
- Run: `github-0853c04f40-issue-2`
- Base commit: `42155f201a0da9db0e969d8cf9eb2907465337c5`
- Factory result: `sha256:17e6ecc33dc46f56de359289f2409cf9f887cee396bfdd8116e6641937833d96`
- Patch: `sha256:2fc5417361957e3ddb392b555e68e90fd2bf7543a304e473518e5773db83409d`
- Draft PR: `https://github.com/mpuig/barista-factory-demo/pull/3`
- Head branch: `barista/issue-2`
- Head commit: `d7329557d97487093c33b14f81525b74d8c1865b`
- Issue comment: `https://github.com/mpuig/barista-factory-demo/issues/2#issuecomment-5453527866`
- Owning and child Factory sessions after delivery: absent

Independent post-run checks confirmed that the PR is open and draft, its base
is `main`, the recorded base/head commits match, its body contains the exact
patch marker, and its only changed file is `issues/issue-2.md`. The issue comment
contains the draft URI, stable run identity, and Factory result digest. The
controller's durable issue status is `succeeded`, public health identifies only
the allowlisted repository, and the Host API session list contains only the
pre-existing paused `counter` session.
