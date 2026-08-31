# managed-acceptance Specification Delta

## ADDED Requirements

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
