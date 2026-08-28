## ADDED Requirements

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
