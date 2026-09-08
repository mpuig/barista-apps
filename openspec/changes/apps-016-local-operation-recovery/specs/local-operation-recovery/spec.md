## ADDED Requirements

### Requirement: Creation identity survives an uncertain result
The provider SHALL persist the session identity, original workload request, and supplied idempotency key atomically before starting the workload. A retry SHALL use that same identity and request, including after a provider restart. The node identity SHALL satisfy the execution backend's identifier contract.

#### Scenario: Startup response is lost
- **WHEN** the execution backend starts a workload but the provider cannot observe the response
- **THEN** retrying the same idempotency key after restart resolves the original workload without starting a second instance

#### Scenario: Startup request never arrives
- **WHEN** the provider persists the request but cannot deliver it to the execution backend
- **THEN** retrying after restart delivers the original request under its reserved identity

### Requirement: Each lifecycle operation has a durable replay identity
The provider SHALL distinguish new pause or resume operations from retries, persist their identities before dispatch, and reuse each identity only for that operation. Completing an uncertain operation SHALL record the current observed workload state.

#### Scenario: Repeated lifecycle cycles
- **WHEN** a caller pauses, resumes, and pauses a session using distinct request keys
- **THEN** all three transitions execute and the session ends paused

#### Scenario: Lifecycle response is lost
- **WHEN** a lifecycle operation completes but its response is lost
- **THEN** retrying after provider restart uses the original backend operation key
