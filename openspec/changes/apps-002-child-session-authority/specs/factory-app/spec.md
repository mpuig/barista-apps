## MODIFIED Requirements

### Requirement: Mission permissions and budgets SHALL bound delegation

A mission SHALL declare allowed repositories, adapters, models, secrets,
network permissions, worker concurrency, attempts, deadlines, and spend/resource
budgets. Workers SHALL receive narrower delegated grants and SHALL not inherit
the coordinator's full authority.

Factory's manifest SHALL declare the delegated authority its workers receive, so
that this bound is enforced by the provider from the manifest rather than relying
on the coordinator's own conduct. Factory SHALL NOT mint credentials for its
workers; it requests a worker session and the provider mints that worker's
narrower grant.

#### Scenario: worker cannot create unbounded descendants
- **WHEN** a worker without child-create permission calls session create
- **THEN** the provider denies it even though the coordinator may create workers

#### Scenario: a worker's authority comes from the manifest, not the coordinator
- **WHEN** a worker session is created for a mission
- **THEN** the credential it receives carries only the actions Factory's manifest declares for its workers, and Factory never handles a credential it did not receive itself
