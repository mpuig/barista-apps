## Purpose

Defines the open wire contract through which portable apps control sessions and artifacts without addressing a privileged Barista node directly.

## ADDED Requirements

### Requirement: Providers SHALL expose version and capability discovery

The Host API SHALL expose contract versions, core profile support, optional
capabilities, quantitative limits, and namespaced extensions before an app
creates resources. Capabilities SHALL reflect the selected account/provider
context rather than every capability somewhere in a fleet.

#### Scenario: app negotiates before creating
- **WHEN** an app connects to a provider
- **THEN** it can determine whether its manifest requirements and limits are satisfied without creating a session

### Requirement: Core session operations SHALL be provider-neutral

The core profile SHALL define ensure/create, get/list/delete, exec, attach,
events, pause/resume, and artifact registration using stable request, response,
operation, and error schemas. Mutations SHALL accept idempotency keys. Providers
MAY automate lifecycle policy but SHALL preserve the observable contract.

#### Scenario: idempotent ensure crosses providers
- **WHEN** an app replays the same ensure request and idempotency key to a conformant provider
- **THEN** it receives the same logical session and operation rather than a duplicate

### Requirement: Optional state powers SHALL use explicit profiles

Snapshot, fork, capsule import/export, delegated grants, story publication, and
branch evaluation SHALL be individually discoverable profiles. A provider SHALL
return a standard capability error when an unavailable profile is invoked and
SHALL not fake it with weaker semantics.

#### Scenario: semantic restart is not exact fork
- **WHEN** a provider cannot restore exact memory and receives an exact fork request
- **THEN** it returns a capability or compatibility error rather than cold-starting a child and reporting success

### Requirement: Streaming and error semantics SHALL be resumable

Events SHALL carry stable cursors and operations SHALL be readable after client
disconnect. Attach SHALL provide a byte-clean non-PTY mode and a terminal mode.
Errors SHALL classify authentication, authorization, capability, compatibility,
conflict, quota, retryable unavailability, and terminal failure.

#### Scenario: client resumes operation observation
- **WHEN** an app disconnects during capsule export and reconnects with the operation id and last event cursor
- **THEN** it continues observation without resubmitting the export or silently skipping events

### Requirement: The Host API SHALL not expose provider internals

Requests SHALL address logical sessions, capsules, apps, and artifacts. Node
addresses, object-store credentials, database ids, and privileged Contract A
credentials SHALL not be portable fields. Provider-specific extensions SHALL
be namespaced and optional.

#### Scenario: local and Cloud handles share one shape
- **WHEN** an app reads session detail from local and Cloud providers
- **THEN** both responses satisfy the same logical schema without exposing Unix sockets or fleet node addresses

