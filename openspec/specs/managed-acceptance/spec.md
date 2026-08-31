# managed-acceptance Specification

## Purpose
TBD - created by archiving change apps-013-managed-demo-smoke. Update Purpose after archive.
## Requirements
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

### Requirement: Managed demo rehearsal SHALL have a no-spend preflight

The managed smoke command SHALL provide a profile that runs the default
lifecycle, dependency-gated Factory, and configured public URL checks, then
warms each configured installed agent app without making a model inference.
Each warm-up SHALL prove readiness, an exact non-secret marker, pause/resume,
and unconditional deletion.

#### Scenario: operator rehearses the showcase environment

- **WHEN** the operator selects the preflight profile with reviewed agent version and binding probes
- **THEN** the provider materializes every configured immutable app and resolves its declared bindings
- **AND** the report identifies the result as preflight rather than model evidence
- **AND** every created session is deleted

#### Scenario: a binding is unavailable

- **WHEN** a reviewed preflight probe finds a required provider environment binding absent
- **THEN** the command does not emit its expected marker
- **AND** the preflight fails and still deletes the session

