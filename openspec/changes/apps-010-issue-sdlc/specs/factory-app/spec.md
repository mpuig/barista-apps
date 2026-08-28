## ADDED Requirements

### Requirement: Factory SHALL orchestrate a bounded issue SDLC attempt

Factory SHALL provide a typed issue SDLC operation that runs triage before implementation, runs implementation only after a validated `ready` decision, and runs coordinator-owned acceptance before declaring a change verified for review. Triage and implementation SHALL execute in isolated child sessions with separately scoped credentials.

#### Scenario: a clear issue produces a verified draft candidate

- **WHEN** triage returns a valid ready decision and implementation passes coordinator-owned acceptance
- **THEN** Factory returns the verified patch, exact base and evidence, and pending runner-owned draft delivery identity

#### Scenario: an unclear issue stops before implementation

- **WHEN** triage returns a valid needs-input decision
- **THEN** Factory runs no implementation command and returns a bounded digest-identified question document for the declared runner-owned issue-comment delivery

### Requirement: Human clarification SHALL resume as a new correlated attempt

Waiting for issue clarification SHALL NOT require a live or paused Factory session. The persistent trigger SHALL record the attempt's canonical result and question, clean the successfully collected attempt, and launch a new correlated attempt only after a signed authorized answer event. The answer SHALL be bounded inert objective context and SHALL NOT modify trusted policy fields.

#### Scenario: an authorized answer advances once

- **WHEN** an allowlisted responder comments on an issue awaiting input
- **THEN** exactly one next attempt is launched with immutable links to the prior attempt and answer comment

#### Scenario: an unauthorized or duplicate comment arrives

- **WHEN** a non-allowlisted responder, bot marker comment, or duplicate delivery is received
- **THEN** no attempt is launched and no existing attempt state changes

### Requirement: Failed development SHALL stop mutation and ask safely when recoverable

A worker or independent acceptance failure classified as recoverable SHALL stop further development and produce a bounded sanitized question document for explicit runner-owned issue-comment delivery. An integrity, authority, secret-scan, or repository-identity failure SHALL publish neither a question nor code.

#### Scenario: independent tests fail

- **WHEN** implementation produces a patch but coordinator-owned acceptance fails
- **THEN** no branch or pull request is delivered, the attempt evidence is preserved, and the controller may post only the independently verified sanitized failure question

#### Scenario: patch integrity fails

- **WHEN** the collected patch or result identity fails independent verification
- **THEN** neither draft delivery nor issue-comment delivery occurs

### Requirement: Factory verification SHALL remain separate from pull-request approval

A successful SDLC attempt MAY report that a patch is verified for review and MAY request a draft pull request through explicit delivery. It SHALL NOT submit a forge approval review or merge its own change unless a separately authenticated policy authority is explicitly introduced.

#### Scenario: verified implementation succeeds

- **WHEN** all Factory checks pass and runner delivery succeeds
- **THEN** the pull request remains draft and the evidence identifies Factory verification without representing human approval
