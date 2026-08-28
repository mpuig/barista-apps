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

### Requirement: A mission SHALL be able to express order between tasks

Factory SHALL accept tasks that declare the tasks they depend on, and SHALL not
start a task before every task it depends on has succeeded. Tasks that do not
depend on one another SHALL still run concurrently, bounded by the mission's
concurrency. A mission whose dependencies cannot be satisfied — a cycle, an
unknown task id, or a task depending on itself — SHALL be refused when the
mission is loaded, before any worker is created.

#### Scenario: a dependent task runs after the task it depends on

- **WHEN** a mission declares that task B depends on task A
- **THEN** B is not started until A has succeeded, and B runs once A has

#### Scenario: independent tasks still run concurrently

- **WHEN** a mission declares tasks that depend on nothing
- **THEN** they run concurrently up to the mission's concurrency, as they did before dependencies existed

#### Scenario: an impossible dependency graph is refused before any work starts

- **WHEN** a mission declares a dependency cycle, a dependency on an unknown task id, or a task that depends on itself
- **THEN** loading the mission fails, naming the offending tasks, and no worker session is created

#### Scenario: a task whose dependency failed is reported as unreachable, not as failed

- **WHEN** a task's dependency does not succeed
- **THEN** the dependent task is reported as blocked, naming the dependency it waited on, and is distinguishable from a task that ran and failed and from one still to be attempted

### Requirement: A dependent task SHALL receive what the task it depends on produced

Factory SHALL let a task declare outputs to capture on success and let a
dependent task declare which of those outputs to receive, placed in the
dependent's own session before its command runs. Outputs SHALL be carried
through the durable coordinator scope, so that a producing worker may be reaped
before its consumer starts.

#### Scenario: a stage hands its output to the next stage

- **WHEN** task A declares an output and task B depends on A and consumes that output
- **THEN** B's session contains that content before B's command runs

#### Scenario: the producer may be reaped before the consumer runs

- **WHEN** task A has succeeded and its worker has been reaped, and task B consumes A's output
- **THEN** B still receives the content

#### Scenario: consuming an output that was never produced is refused before any work starts

- **WHEN** a task consumes an output that no task it depends on produces
- **THEN** loading the mission fails, naming the task and the missing output, and no worker session is created

### Requirement: A task SHALL be judged by a criterion it did not write

Factory SHALL let a mission plant content into a worker's session before the
worker's command runs, and SHALL ensure the planted content is the mission's own
when the task's check runs, whatever the worker did to it in between.

A mission SHALL additionally be able to require that every check in it names
only planted content or content received from a dependency, and such a mission
SHALL be refused at load if any check names anything else. This requirement is
one a mission opts into: a check may name a path as the location to inspect
rather than as the criterion, and those two uses are not distinguishable by
shape.

The isolation a worker runs under bounds what its work can reach; it does not
make the worker's own account of that work into evidence. A criterion fixed by
the mission rather than by the worker is what makes a passing check mean
something.

#### Scenario: the check runs against the mission's criterion, not the worker's

- **WHEN** a mission plants a check's input and the worker overwrites it before the check runs
- **THEN** the check runs against the content the mission planted

#### Scenario: a mission that requires self-authored checks to be impossible is refused when one is present

- **WHEN** a mission requires that checks name only fixed content, and one of its checks names a path that is neither planted nor received from a dependency
- **THEN** loading the mission fails, naming the task and the path, and no worker session is created

#### Scenario: a mission that does not require it is not refused for naming a path

- **WHEN** a mission does not require that checks name only fixed content, and a check names a path as the location it inspects
- **THEN** the mission loads and runs

#### Scenario: planting survives a coordinator restart without duplicating work

- **WHEN** a coordinator restarts after planting content for a task that has not yet succeeded
- **THEN** the task's worker is re-ensured with the same planted content and the task is not run a second time after it has succeeded

### Requirement: Factory SHALL consume repository work through the shared App Run binding contract

Factory's software-change operation SHALL accept a repository workspace and an
objective as declared App Run bindings. It SHALL resolve one exact base commit
for the mission and SHALL give every worker an equivalent base without requiring
a provider-specific fork capability.

#### Scenario: issue-driven software change

- **WHEN** a Factory run binds a Git repository and a forge issue objective
- **THEN** Factory records the exact base commit and treats the issue as untrusted objective content within its bounded workflow

### Requirement: Factory SHALL integrate worker output before independent verification

Parallel workers SHALL NOT share a writable checkout. Factory SHALL collect
declared patches or artifacts, apply selected changes to a clean integration
workspace based on the resolved commit, and run coordinator-owned acceptance
against that integrated tree.

#### Scenario: worker cannot approve its own modified test

- **WHEN** a worker changes implementation and weakens a test in its own workspace
- **THEN** the independent integration check uses the coordinator-owned criterion and does not accept the weakened test as evidence

### Requirement: Factory SHALL publish only an explicitly requested verified delivery

Factory SHALL create a draft pull request only when the run explicitly requests
a declared pull-request delivery and independent integration checks pass. The
pull request SHALL identify the objective, exact base and head revisions,
app/workload identity, checks, and receipt references.

#### Scenario: successful issue run returns a draft pull request

- **WHEN** the integrated change passes independent checks and draft-pull-request delivery was requested
- **THEN** Factory publishes that verified head and returns its external reference in the canonical result

#### Scenario: failed issue run preserves evidence without publication

- **WHEN** integration or independent verification fails under the default policy
- **THEN** Factory creates no pull request, records the failure, and preserves bounded forensic evidence and recoverable output

### Requirement: Factory SHALL preserve its established mission delivery contract

The generic App Run adapter for Factory SHALL map the validated mission input to
`$BARISTA_FACTORY_MISSION` before the coordinator starts. Explicit mission paths
SHALL retain their existing error behavior and SHALL NOT silently fall back to
the generic run input.

#### Scenario: generic launch reaches the canonical mission input

- **WHEN** the portable runner launches Factory with a valid mission in its App Run envelope
- **THEN** the coordinator receives the identical mission through `$BARISTA_FACTORY_MISSION`

