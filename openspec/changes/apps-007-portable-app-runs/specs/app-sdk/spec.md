## ADDED Requirements

### Requirement: The SDK SHALL validate and canonicalize App Runs before mutation

The SDK SHALL expose immutable models for App Runs, bindings, secret references,
delivery requests, and results. It SHALL validate a run against its selected
manifest operation and serialize it canonically before calling an installation,
session, binding, or delivery mutation.

#### Scenario: validation leaves no partial run

- **WHEN** SDK validation finds an undeclared binding or invalid input
- **THEN** it raises a typed invalid-request error before issuing a provider mutation

### Requirement: The SDK SHALL launch a run through existing Host API resources

The SDK SHALL resolve and install the app as needed, ensure one owning session
under a stable idempotency key, deliver the canonical run envelope at launch,
and observe completion according to the operation lifecycle. It SHALL NOT
require a provider-specific run endpoint.

#### Scenario: retry does not duplicate an owning session

- **WHEN** a launch response is lost and the SDK retries with the same idempotency key
- **THEN** it receives the original owning session rather than creating another run

### Requirement: The SDK SHALL verify result integrity before cleanup

For terminal operations, the SDK SHALL retrieve the canonical result from the
owning session, compare its bytes with the registered content digest, persist
requested output, and only then perform configured cleanup.

#### Scenario: cleanup cannot erase the only result copy

- **WHEN** result retrieval or digest verification fails
- **THEN** the SDK preserves the owning session and reports collection failure
