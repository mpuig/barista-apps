## ADDED Requirements

### Requirement: A manifest SHALL declare typed run operations independently of workload launch

An App Manifest MAY declare named run operations. Each operation SHALL declare a
lifecycle, accepted input media type, accepted binding names and kinds, and
accepted delivery names and kinds. It MAY embed a Draft 2020-12 input schema.
The declaration SHALL describe the application contract without changing the
manifest's digest-pinned workload identity.

#### Scenario: one app exposes different operations

- **WHEN** an app supports both an interactive operation and a terminating review operation
- **THEN** its manifest can declare each with its own lifecycle, input, bindings, and deliveries

#### Scenario: app-specific detail remains opaque

- **WHEN** an operation accepts a namespaced Git repository binding and app-specific input media type
- **THEN** a provider preserves and delivers them without needing to understand Git or the input document

### Requirement: A manifest SHALL NOT claim provider-reserved App Run context

An App Manifest SHALL NOT declare `BARISTA_APP_SESSION_ID` as a secret channel
or otherwise require a caller value for it. The provider alone injects that
non-secret opaque handle after allocating the app's session.

#### Scenario: secret channel collides with owning-session context

- **WHEN** a manifest declares a secret whose environment name is `BARISTA_APP_SESSION_ID`
- **THEN** installation is refused before a session or secret is created

### Requirement: Run declarations SHALL be additive to existing manifests

The `runs` declaration SHALL be optional. A manifest without it SHALL retain its
existing installation and direct session-launch behavior. A typed App Run runner
SHALL refuse to guess an operation for such a manifest.

#### Scenario: an older manifest still installs

- **WHEN** a valid pre-run-declaration manifest is installed after this change
- **THEN** it remains valid and can be launched through the existing session API

#### Scenario: typed run needs a declaration

- **WHEN** a caller asks the generic runner to run an app whose manifest declares no run operation
- **THEN** the runner reports that the app has no typed operation and creates no session
