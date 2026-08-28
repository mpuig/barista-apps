## ADDED Requirements

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
