# Design: Repeatable managed demo smoke gate

## Decisions

### D1 — Keep managed and standalone evidence separate

The managed command requires an endpoint and token and shells only the existing
managed pytest nodes. It does not enter the standalone process, weaken the
Cloud network guard, or make managed infrastructure a default developer test.

### D2 — A skip is terminal in the operator command

Individual managed tests continue to skip when a developer has not configured
infrastructure. `barista-managed-smoke` is an explicit release-gate invocation,
so its subprocess requests skip reasons and fails if any selected check skips.
A zero pytest exit code is insufficient evidence.

### D3 — Profiles separate cost and elapsed-time classes

The default profile runs lifecycle and Factory checks. The model profile adds
configured agent checks, and the slow profile adds real elapsed-time grant
renewal. Each report names its profile. Model checks create and delete sessions
for already-installed apps; credentials are resolved from manifest secret
references by the provider and never appear in runner configuration.

### D4 — Reports are bounded and replace atomically

Each step records name, terminal state, duration, and bounded evidence. Pytest
output is tail-capped. Optional output files are written via a sibling temporary
file and replaced only after complete JSON encoding.

### D5 — Public checks are named and explicit

Operators may add controller, generated-app, health, and publication URLs. The
runner performs bounded HTTP reads and records only status and sampled byte
count; it does not infer application semantics from an HTTP 200.

## Failure behavior

The command stops after the first failed step, records that failure, emits the
partial report, and exits nonzero. Successful test-owned sessions are cleaned by
the selected acceptance cases. Existing failed-evidence retention remains an
operator workflow until a provider-neutral bounded-retention contract exists.
