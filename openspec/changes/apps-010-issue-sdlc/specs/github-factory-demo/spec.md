## ADDED Requirements

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
