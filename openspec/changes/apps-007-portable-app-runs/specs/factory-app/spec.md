## ADDED Requirements

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
