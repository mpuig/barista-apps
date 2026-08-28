# app-manifest Specification

## Purpose

Defines a portable, least-privilege declaration for packaging and running a Barista application on any conformant host provider.
## Requirements
### Requirement: Every app SHALL pin executable content

An App Manifest SHALL identify its workload as an OCI image by immutable digest
and SHALL carry a manifest schema version, app name, app version, supported
architectures, entrypoints, working directory, readiness contract, and optional
HTTP, WebSocket, MCP, or ACP endpoints. Mutable image tags SHALL be labels only
and SHALL NOT establish app identity.

#### Scenario: mutable image is rejected as identity
- **WHEN** a manifest supplies only `example/app:latest` without a digest
- **THEN** validation fails before any session is created

### Requirement: Capabilities SHALL be declared as required or optional

The manifest SHALL distinguish required host capabilities from optional ones
and MAY declare minimum contract versions and semantic constraints. A provider
SHALL reject installation before side effects when a requirement is unmet; the
app SHALL receive the discovered optional set at launch.

#### Scenario: missing required fork fails before launch
- **WHEN** an app requires `session.fork` and the selected provider does not advertise it
- **THEN** installation or launch fails with a capability error and creates no session

### Requirement: Permissions SHALL be least-privilege declarations

The manifest SHALL declare requested Host API actions, network policy, secret
references, artifact access, publication surfaces, and child-session limits.
It SHALL contain only secret names or references, never secret plaintext. A host
MAY grant less than requested only when the app declared a valid degraded mode.

#### Scenario: undeclared child creation is denied
- **WHEN** an app without `session.create` permission tries to create a worker
- **THEN** the host denies the request regardless of the user's broader privileges

### Requirement: App-specific metadata SHALL remain opaque to hosts

Harness configuration, transcript formats, model identifiers, and semantic
continuation data SHALL live in namespaced app metadata or artifacts. Providers
SHALL preserve recognized envelope constraints without needing to understand
Pi, Claude, Codex, or any other harness.

#### Scenario: third-party adapter needs no provider update
- **WHEN** a new adapter adds metadata under its registered app namespace
- **THEN** a conformant provider can store and return it without a Host API schema change

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

