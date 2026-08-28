# github-factory-demo Specification

## Purpose
TBD - created by archiving change apps-008-github-factory-demo. Update Purpose after archive.
## Requirements
### Requirement: The GitHub Factory controller SHALL authenticate and bound webhook triggers

The controller SHALL verify the exact request bytes with the configured webhook
secret before parsing, SHALL accept only configured repository `issues.opened`
events, and SHALL durably deduplicate both delivery ID and repository/issue.

#### Scenario: valid new issue is accepted once

- **WHEN** GitHub sends a correctly signed `issues.opened` event for the configured repository
- **THEN** the controller durably accepts it, returns promptly, and launches exactly one Factory run across webhook retries

#### Scenario: forged or out-of-scope webhook is inert

- **WHEN** a signature is invalid, the event/action is unsupported, or the repository differs
- **THEN** no App Run, Git mutation, comment, or delivery is created

### Requirement: The controller SHALL launch an ephemeral bounded Factory run

The controller SHALL compile trusted configuration and the issue identity into
the canonical Factory `software-change` operation. Issue content SHALL NOT
select commands, repository, base, checks, credentials, or delivery target.

#### Scenario: issue content cannot widen the run

- **WHEN** an issue asks to publish elsewhere, skip checks, or reveal a token
- **THEN** Factory still uses the configured repository, worker command,
  coordinator-owned acceptance, and declared draft target

### Requirement: The controller SHALL deliver only independently verified output

The controller SHALL collect and validate Factory's canonical succeeded result,
retrieve the integrated patch with size and digest verification, recheck the
resolved repository identity, and only then fulfill the explicitly declared
draft delivery through the forge adapter.

#### Scenario: successful issue produces a draft and cleanup

- **WHEN** Factory integration checks succeed and the patch matches its result
- **THEN** the controller creates one draft pull request, records exact base/head
  and result identities, comments the issue, and deletes the owning session

#### Scenario: failure preserves evidence without publication

- **WHEN** Factory fails, result collection fails, patch integrity fails, or the
  forge refuses publication
- **THEN** no new pull request is accepted as the result and the owning session
  is preserved for bounded forensics

### Requirement: Demo setup SHALL be explicit, digest-pinned, and reversible

The bootstrap CLI SHALL create or select a repository, push deterministic seed
content, install a signed webhook, and install digest-pinned Factory and worker
manifests without printing credentials. Teardown SHALL remove only resources
recorded in bootstrap state and SHALL require a separate explicit choice to
delete the repository.

#### Scenario: setup creates a repeatable demo

- **WHEN** an operator supplies GitHub bootstrap authority, webhook URL/secret,
  and exact app image digests
- **THEN** the resulting repository can trigger repeated issue-to-draft runs and
  setup records enough non-secret identity for explicit teardown

