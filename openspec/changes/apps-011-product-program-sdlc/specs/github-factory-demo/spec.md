# github-factory-demo delta

## ADDED Requirements

### Requirement: durable human-approved product programs

The controller SHALL persist product programs and SHALL treat only a fresh, correlated, authorized merge of the expected verified BRD pull request as BRD approval.

#### Scenario: manual project movement is inert

- **WHEN** a user changes a GitHub Project Status field
- **THEN** no canonical program transition occurs
- **AND** reconciliation restores the field from controller state

#### Scenario: correlated BRD merge approves planning

- **WHEN** an authorized merger merges the exact expected BRD pull request
- **THEN** the controller records approved bytes, digest, commit, actor, and time
- **AND** creates one fresh planning attempt

### Requirement: idempotent dependency-gated feature delivery

The controller SHALL independently validate a bounded acyclic plan, publish each feature issue idempotently, and release a feature only after all predecessor merges are durably accepted.

#### Scenario: retry converges

- **WHEN** planning or feature delivery is retried after an uncertain response
- **THEN** stable identities converge on the same plan, issues, and receipts
- **AND** no duplicate feature is intentionally created

#### Scenario: unmet dependency blocks execution

- **WHEN** any predecessor is not accepted as merged
- **THEN** the dependent feature remains blocked
- **AND** no implementation App Run is created

### Requirement: optional non-authoritative Projects projection

When Projects projection is configured, the controller SHALL project canonical issue state into one configured GitHub Projects v2 project using a separate controller-only credential.

#### Scenario: projection fails

- **WHEN** a Projects API read or mutation fails
- **THEN** a sanitized durable projection failure is recorded
- **AND** the canonical workflow transition remains committed

#### Scenario: controller restarts

- **WHEN** projection is configured after a restart
- **THEN** the controller derives desired project fields from durable workflow state
- **AND** reconciles drift without reading authority from project fields

### Requirement: assembled-product acceptance

The controller SHALL run final acceptance only for the exact assembled commit after all required feature merges.

#### Scenario: acceptance has least authority

- **WHEN** final acceptance executes
- **THEN** it receives no forge, model, project, or Host API credential
- **AND** its bounded result is independently verified before program completion
