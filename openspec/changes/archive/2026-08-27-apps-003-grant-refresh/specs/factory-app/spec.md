## ADDED Requirements

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
