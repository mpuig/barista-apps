# factory-app Specification

## Purpose

Defines the OSS software-factory application that coordinates durable worker sessions through only the portable Host API.
## Requirements
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

### Requirement: A mission SHALL be deliverable through the session environment

Factory SHALL accept its mission from the environment variable
`BARISTA_FACTORY_MISSION`, whose value is the mission document as JSON. A
provider delivers it the same way it delivers any other session environment —
atomically with session creation — so a coordinator never starts without the
mission it is meant to run.

Factory SHALL also accept a filesystem path, for an operator invoking the binary
directly. When a path is given explicitly and cannot be read, that SHALL be an
error: it SHALL NOT fall back to the environment, because a stale environment
running in place of the mission an operator named is worse than a failure.

An app that cannot be told what to do is not portable, and this is the mechanism
that requires nothing of the provider beyond session environment, which the
Host API already guarantees.

#### Scenario: a coordinator session receives its mission
- **WHEN** a session is created from Factory's manifest with
  `BARISTA_FACTORY_MISSION` set to a valid mission document
- **THEN** the coordinator runs that mission without any file being placed in
  the session beforehand

#### Scenario: a named mission file that is missing is an error
- **WHEN** the binary is invoked with an explicit mission path that does not
  exist, while `BARISTA_FACTORY_MISSION` is set to a different valid mission
- **THEN** it fails naming the path, and does not run the mission from the
  environment

### Requirement: Missing configuration SHALL be reported as an app failure

Factory SHALL validate the configuration it requires — at minimum the Host API
endpoint and the mission — before performing any work that could terminate the
process, and SHALL report a missing value by naming it.

A workload that raises on startup exits, and an exited workload ends its
instance; a provider then reports the session as having no reachable guest. That
is accurate about the instance and misleading about the cause, and it sends an
operator to the wrong layer. The requirement is not that a misconfigured app
keeps running — it is that the last thing it writes says which variable was
missing.

#### Scenario: no endpoint configured
- **WHEN** a coordinator starts with no Host API endpoint configured
- **THEN** it reports the missing variable by name and exits non-zero, rather
  than terminating with only a provider-level unreachable-guest report as
  evidence

### Requirement: A mission SHALL survive the coordinator's session pausing

A mission SHALL continue across a pause and resume of the coordinator's own
session, reconstructing task state from durable storage rather than restarting
work. An attempt already accepted SHALL NOT produce a second worker.

This is the durability claim made observable. *One durable coordinator SHALL own
mission state* already requires reconstruction after "pause or restart"; this
names the pause of the session itself — the case the product's own promise rests
on and the one a process-level restart does not exercise.

#### Scenario: paused mid-mission and resumed
- **WHEN** the coordinator's session is paused while tasks are in flight, and
  resumed
- **THEN** the mission completes, and every accepted attempt is served by the
  worker that already had it

### Requirement: A mission SHALL outlive a single delegated grant lifetime

Where a provider advertises `grants.delegated`, a mission running longer than
one grant lifetime SHALL renew its credential and continue, and SHALL report
that it did so.

Renewal SHALL be demonstrated by elapsed time against the provider's real grant
lifetime. A shortened lifetime configured for the test exercises the margin
arithmetic while leaving untested the thing the requirement exists for: that a
long-running coordinator is still authorized an hour later.

#### Scenario: a mission longer than one grant lifetime
- **WHEN** a mission runs for longer than the provider's grant lifetime minus
  the renewal margin
- **THEN** it completes, having renewed more than once, with authority never
  reported lost and no reason recorded for the credential being inactive

### Requirement: The same mission SHALL be demonstrated on a managed provider

The portability claim SHALL be exercised against a managed provider, not only a
local one. Acceptance SHALL include a run that installs Factory's manifest,
creates a coordinator session from it, and runs a mission to completion through
the Host API alone.

A run requiring a provider and a credential SHALL be separable from the offline
standalone flow, and SHALL state its reason when skipped. The standalone flow's
guarantee — that the open stack runs with Barista Cloud unreachable — SHALL NOT
be weakened to accommodate it.

#### Scenario: managed acceptance run
- **WHEN** acceptance runs against a provider advertising `core`,
  `session.pause_resume` and `grants.delegated`
- **THEN** a coordinator session created from Factory's manifest runs a mission
  to completion, with receipts harvested and workers reaped

#### Scenario: no provider configured
- **WHEN** no Host API endpoint is configured
- **THEN** the managed run is skipped naming that reason, and the offline
  standalone flow still runs and still passes

### Requirement: A mission SHALL be able to outlive one grant lifetime

Factory SHALL refresh its delegated credential while a mission is running, so
that a mission whose duration exceeds a single grant lifetime continues rather
than failing partway. Factory SHALL refresh before its current credential
lapses, since a lapsed grant cannot be refreshed.

#### Scenario: a mission longer than one grant lifetime completes

- **WHEN** a mission runs for longer than the provider's delegated grant lifetime
- **THEN** the coordinator continues to act on its workers throughout, without an operator supplying a new credential

#### Scenario: a lapsed credential is reported, not retried into failure

- **WHEN** the coordinator's credential has lapsed before it refreshed
- **THEN** the mission reports that it lost its authority, rather than reporting the work itself as failed

