## ADDED Requirements

### Requirement: A manifest SHALL be able to declare what a child session receives

An app that creates child sessions SHALL be able to declare, in its manifest, the
set of actions a child session receives. That set SHALL be a subset of the actions
the app itself declares. A manifest declaring a child action the app does not hold
SHALL be refused at install, naming the offending actions, rather than accepted
and refused later at use.

#### Scenario: a child receives a narrower set than its parent

- **WHEN** an app declares actions for itself and a smaller set for its children
- **THEN** the manifest is accepted, and a child session receives only the smaller set

#### Scenario: a child cannot be given more than its parent holds

- **WHEN** a manifest declares a child action the app itself does not declare
- **THEN** the install is refused and the response names the action that exceeded the app's own authority

#### Scenario: declaring no child authority is unchanged behaviour

- **WHEN** a manifest declares child session limits but no child actions
- **THEN** it remains valid and its children receive no delegated authority, exactly as before this change

### Requirement: A manifest SHALL be able to state whether a child may create descendants

An app SHALL be able to declare whether a session it creates may itself create
further sessions. Absent an explicit declaration, a child SHALL NOT be able to
create descendants.

#### Scenario: descendants are refused by default

- **WHEN** a child session created by an app calls session create, and the manifest did not grant descendant creation
- **THEN** the provider denies it, even though the app itself may create sessions

#### Scenario: a declared descendant depth is honoured

- **WHEN** a manifest explicitly permits its children to create sessions
- **THEN** a child's session create is permitted, subject to the app's own child session limits

### Requirement: Declared actions SHALL be scopable to the sessions an app creates

An app SHALL be able to declare that an action applies to the sessions it creates,
not only to its own session. A provider SHALL scope the delegated authority it
mints accordingly, and SHALL NOT widen it to sessions the app did not create.

#### Scenario: a coordinator can act on the sessions it created

- **WHEN** an app declares an action over the sessions it creates and then acts on one of them
- **THEN** the action is authorized

#### Scenario: authority stops at the app's own children

- **WHEN** that same app attempts the same action against a session it did not create
- **THEN** the action is refused
