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

