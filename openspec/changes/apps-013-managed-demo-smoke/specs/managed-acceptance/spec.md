# managed-acceptance Specification

## ADDED Requirements

### Requirement: Managed releases SHALL have a machine-readable smoke gate

The repository SHALL provide one opt-in command that runs a managed session
lifecycle check and a dependency-gated Factory mission through the public Host
API. It SHALL emit a bounded report naming its profile, each selected step,
duration, evidence, and terminal state. A skipped selected check SHALL fail the
gate rather than count as passing.

#### Scenario: provider lacks delegated grants

- **WHEN** the default managed profile selects the Factory mission but the authenticated provider does not advertise delegated grants
- **THEN** the Factory check skips at the pytest layer
- **AND** the managed smoke command records failure and exits nonzero

### Requirement: Managed evidence classes SHALL remain explicit

The command SHALL distinguish default, model, and slow profiles. Model checks
SHALL target preinstalled apps and SHALL accept no model credential value. Slow
real-elapsed-time checks SHALL remain outside the default profile.

#### Scenario: operator selects real model checks

- **WHEN** the model profile runs Claude, Pi, or Codex argv against a preinstalled app
- **THEN** the provider resolves that app's secret references
- **AND** the report records only app identity, expected marker, operation identity, bounded output size, and lifecycle result

### Requirement: Managed smoke resources SHALL be disposable

A successful managed lifecycle, Factory, or model check SHALL delete every
session it created after collecting evidence. Cleanup SHALL execute even when a
model check raises after session creation.

#### Scenario: model output lacks its expected marker

- **WHEN** a model operation settles but bounded stdout does not contain the configured marker
- **THEN** the step fails
- **AND** its created session is still deleted
