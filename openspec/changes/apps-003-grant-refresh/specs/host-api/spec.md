## ADDED Requirements

### Requirement: A session SHALL be able to refresh a delegated grant it holds

A provider that advertises delegated grants SHALL offer an operation that
accepts a live delegated grant as the caller's credential and returns a
replacement credential. The replacement SHALL carry exactly the resource and
actions recorded for the presented grant, and SHALL NOT accept any scope from
the request. The presented credential SHALL stop being accepted once the
replacement is issued.

#### Scenario: a held grant is replaced with an equivalent one

- **WHEN** a session presents a live delegated grant to the refresh operation
- **THEN** it receives a replacement credential authorizing the same actions on the same resource, with a later expiry

#### Scenario: the previous credential stops working

- **WHEN** a grant has been refreshed and its previous secret is presented again
- **THEN** it is refused

#### Scenario: refresh cannot widen authority

- **WHEN** a refresh request carries a resource or action set of its own
- **THEN** the replacement still authorizes only what the presented grant already authorized

### Requirement: A grant that is no longer live SHALL NOT be refreshable

Refresh SHALL be refused for a grant that has expired or been revoked, so that
expiry and revocation remain final.

#### Scenario: an expired grant cannot be revived

- **WHEN** a grant whose expiry has passed is presented to the refresh operation
- **THEN** the refresh is refused and no replacement is issued

#### Scenario: a revoked grant cannot be revived

- **WHEN** a grant that was revoked is presented to the refresh operation
- **THEN** the refresh is refused

### Requirement: Refresh SHALL be gated on the delegated-grants capability

A provider that does not advertise delegated grants SHALL NOT be required to
offer refresh, and an app SHALL be able to discover its availability before
depending on it.

#### Scenario: a provider without delegated grants is unaffected

- **WHEN** a provider does not advertise delegated grants
- **THEN** it is conformant without offering the refresh operation
