## ADDED Requirements

### Requirement: Conformance SHALL clean up partially acquired probes

The conformance suite SHALL record each session it creates for delegated-credential acquisition before performing the next acquisition step. If acquisition fails or raises after any session is created, the suite SHALL attempt to delete every recorded session before the run returns.

Operator-supplied delegated probe sessions SHALL NOT be treated as suite-owned resources and SHALL NOT be deleted by this cleanup.

#### Scenario: credential acquisition raises after coordinator creation
- **WHEN** the suite creates a probe coordinator and the subsequent child-session request raises
- **THEN** the coordinator is still recorded and deleted before the conformance run returns

#### Scenario: operator-supplied probes are retained
- **WHEN** delegated credentials and their session ids are supplied by an operator
- **THEN** the suite does not add those session ids to its cleanup ledger or delete them
