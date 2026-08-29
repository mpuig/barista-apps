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

### Requirement: The GitHub controller SHALL resume clarification through authorized comments

The controller SHALL accept signed `issue_comment.created` events only for its allowlisted repository and only for issues durably awaiting input. It SHALL authorize the commenter against explicit trusted configuration, deduplicate delivery and comment identity, and launch at most one new ephemeral Factory attempt per accepted answer.

#### Scenario: controller question is echoed back as a webhook

- **WHEN** the controller's own idempotent question comment produces an issue-comment webhook
- **THEN** the event is recognized as controller output and does not launch another attempt

#### Scenario: stale answer arrives after work resumed

- **WHEN** an otherwise authorized comment arrives while the issue is not awaiting input
- **THEN** it is acknowledged as ignored and does not alter the current attempt

### Requirement: The controller SHALL independently verify question delivery

Before posting a Factory-produced clarification or failure question, the controller SHALL verify the canonical result state, issue and repository identity, declared issue-comment delivery target and request digest, bounded question-document size and digest, and absence of high-confidence secrets. A verification failure SHALL produce no external side effect.

#### Scenario: worker claims a different issue target

- **WHEN** a question result names an issue or delivery target other than the claimed allowlisted issue
- **THEN** the controller refuses the comment and records a sanitized failure

### Requirement: durable human-approved product programs

The controller SHALL persist product programs and SHALL treat only a fresh, correlated, authorized merge of the expected verified BRD pull request as BRD approval.

#### Scenario: manual project movement is inert

- **WHEN** a user changes a GitHub Project Status field
- **THEN** no canonical program transition occurs
- **AND** reconciliation restores the field from controller state

#### Scenario: correlated BRD merge approves planning

- **WHEN** an authorized merger merges the exact expected BRD pull request
- **THEN** the controller records approved bytes, digest, commit, actor, and time
- **AND** creates one fresh planning attempt

### Requirement: idempotent dependency-gated feature delivery

The controller SHALL independently validate a bounded acyclic plan, publish each feature issue idempotently, and release a feature only after all predecessor merges are durably accepted.

#### Scenario: retry converges

- **WHEN** planning or feature delivery is retried after an uncertain response
- **THEN** stable identities converge on the same plan, issues, and receipts
- **AND** no duplicate feature is intentionally created

#### Scenario: unmet dependency blocks execution

- **WHEN** any predecessor is not accepted as merged
- **THEN** the dependent feature remains blocked
- **AND** no implementation App Run is created

### Requirement: optional non-authoritative Projects projection

When Projects projection is configured, the controller SHALL project canonical issue state into one configured GitHub Projects v2 project using a separate controller-only credential.

#### Scenario: projection fails

- **WHEN** a Projects API read or mutation fails
- **THEN** a sanitized durable projection failure is recorded
- **AND** the canonical workflow transition remains committed

#### Scenario: controller restarts

- **WHEN** projection is configured after a restart
- **THEN** the controller derives desired project fields from durable workflow state
- **AND** reconciles drift without reading authority from project fields

### Requirement: assembled-product acceptance

The controller SHALL run final acceptance only for the exact assembled commit after all required feature merges.

#### Scenario: acceptance has least authority

- **WHEN** final acceptance executes
- **THEN** it receives no forge, model, project, or Host API credential
- **AND** its bounded result is independently verified before program completion

