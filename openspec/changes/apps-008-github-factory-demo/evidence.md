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

Not yet run. Task 5.4 remains open until digest-pinned Factory/worker images and a
stable public HTTPS controller ingress are selected. The opt-in command is
implemented as `barista-github-demo accept`; it creates a real issue and records
exact issue, Factory result, base, patch, draft, head, and session-cleanup
evidence.
