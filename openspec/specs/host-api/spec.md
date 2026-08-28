# host-api Specification

## Purpose

Defines the open wire contract through which portable apps control sessions and artifacts without addressing a privileged Barista node directly.
## Requirements
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

### Requirement: A session SHALL be able to refresh a delegated grant it holds

A provider that advertises delegated grants SHALL offer an operation that
accepts a live delegated grant as the caller's credential and returns a
replacement credential. The replacement SHALL carry exactly the resource and
actions recorded for the presented grant, and SHALL NOT accept any scope from
the request. The presented credential SHALL stop being accepted once the
replacement is issued.

#### Scenario: a held grant is replaced with an equivalent one

- **WHEN** a session presents a live delegated grant to the refresh operation
- **THEN** it receives a replacement credential authorizing the same actions on the same resource, with a later expiry

#### Scenario: the previous credential stops working

- **WHEN** a grant has been refreshed and its previous secret is presented again
- **THEN** it is refused

#### Scenario: refresh cannot widen authority

- **WHEN** a refresh request carries a resource or action set of its own
- **THEN** the replacement still authorizes only what the presented grant already authorized

### Requirement: A grant that is no longer live SHALL NOT be refreshable

Refresh SHALL be refused for a grant that has expired or been revoked, so that
expiry and revocation remain final.

#### Scenario: an expired grant cannot be revived

- **WHEN** a grant whose expiry has passed is presented to the refresh operation
- **THEN** the refresh is refused and no replacement is issued

#### Scenario: a revoked grant cannot be revived

- **WHEN** a grant that was revoked is presented to the refresh operation
- **THEN** the refresh is refused

### Requirement: Refresh SHALL be gated on the delegated-grants capability

A provider that does not advertise delegated grants SHALL NOT be required to
offer refresh, and an app SHALL be able to discover its availability before
depending on it.

#### Scenario: a provider without delegated grants is unaffected

- **WHEN** a provider does not advertise delegated grants
- **THEN** it is conformant without offering the refresh operation

### Requirement: An app workload SHALL receive its own opaque session handle

A provider SHALL allocate and persist a session's opaque Host API handle before
starting its app workload and SHALL inject it as `BARISTA_APP_SESSION_ID`. The
provider SHALL refuse a caller-supplied value for that reserved variable before
session creation. The injected value SHALL identify the Host API session and
SHALL NOT expose a node address or provider-internal identifier.

#### Scenario: workload can register on its own scope

- **WHEN** an app starts and needs to register a result artifact on its own session
- **THEN** `BARISTA_APP_SESSION_ID` contains the session handle accepted by the Host API artifact route

#### Scenario: reserved context cannot be forged

- **WHEN** an ensure request includes `BARISTA_APP_SESSION_ID` in its environment
- **THEN** the request is refused and no session is created

### Requirement: An installed app's manifest SHALL be retrievable for run validation

The Host API SHALL let an authorized caller retrieve the validated manifest and
installed identity for an installed app by provider-scoped app name. The
response SHALL contain no resolved secret values. A runner SHALL be able to use
that manifest to select and validate a declared run operation without reinstalling
the app or relying on a provider-specific catalog.

#### Scenario: installed app is run by name

- **WHEN** a caller selects an already installed app by name
- **THEN** the runner retrieves its validated manifest, validates the selected operation, and launches without reinstalling it

#### Scenario: unknown installed app has no launch side effect

- **WHEN** a caller selects an app name that is not installed and supplies no resolvable manifest source
- **THEN** the Host API returns a standard not-found error and no session is created

#### Scenario: retrieval does not expose credentials

- **WHEN** an installed manifest declares secret references
- **THEN** retrieval returns those references and never the values the provider resolves for a workload

