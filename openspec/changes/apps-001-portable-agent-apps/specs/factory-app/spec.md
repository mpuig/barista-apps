## Purpose

Defines the OSS software-factory application that coordinates durable worker sessions through only the portable Host API.

## ADDED Requirements

### Requirement: Factory SHALL be an ordinary portable app

Factory SHALL use only Host API and SDK operations declared in its manifest. It
SHALL not call Contract A, read provider databases or buckets directly, require
a Cloud API shape, or assume a specific agent adapter.

#### Scenario: same mission runs locally and in Cloud
- **WHEN** a mission and available adapters are identical on conformant local and Cloud providers
- **THEN** Factory accepts the same mission schema and produces the same result/receipt structure

### Requirement: One durable coordinator SHALL own mission state

Factory SHALL persist mission, task graph, worker handles, attempts, deadlines,
status, receipts, and artifact references under a coordinator session or
provider artifact scope. Replaying coordination after restart SHALL ensure
workers idempotently rather than duplicating accepted tasks.

#### Scenario: coordinator resumes after pause
- **WHEN** the coordinator pauses or restarts mid-mission
- **THEN** it reconstructs task state and continues without creating a second worker for an already accepted attempt

### Requirement: Artifacts SHALL be harvested before successful workers are reaped

For every successful worker, Factory SHALL register declared artifacts and the
result receipt durably before deleting the worker. Failed workers SHALL remain
available or be encapsulated according to mission policy for bounded forensics.

#### Scenario: successful worker disk can disappear safely
- **WHEN** a worker succeeds and is deleted
- **THEN** its registered outputs remain retrievable and the receipt proves harvest completed before deletion

### Requirement: Mission permissions and budgets SHALL bound delegation

A mission SHALL declare allowed repositories, adapters, models, secrets,
network permissions, worker concurrency, attempts, deadlines, and spend/resource
budgets. Workers SHALL receive narrower delegated grants and SHALL not inherit
the coordinator's full authority.

#### Scenario: worker cannot create unbounded descendants
- **WHEN** a worker without child-create permission calls session create
- **THEN** the provider denies it even though the coordinator may create workers

