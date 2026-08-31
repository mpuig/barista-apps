# github-factory-demo Specification Delta

## ADDED Requirements

### Requirement: The demo SHALL provide a bounded presenter cockpit

The controller SHALL expose a public read-only presenter surface and bounded
state document derived from controller-authoritative programs, attempts,
events, features, and deployments. The surface SHALL identify the current
stage, next human action, dependency gates, artifacts, deployment, health, and
cleanup status without treating projections as authority.

#### Scenario: audience follows a live program

- **WHEN** a product program advances
- **THEN** the cockpit refreshes from controller state without credentials
- **AND** distinguishes automatic work, blocked dependencies, human approval, failure, and terminal settlement without color-only meaning

### Requirement: Presenter launch SHALL be idempotent and separately authorized

Mutating presenter requests SHALL require a distinct configured bearer token.
Launch SHALL accept only the reviewed scenario and a bounded idempotency key,
create an inert product-program issue through repository-scoped forge
authority, and durably converge duplicate requests on the same scenario.

#### Scenario: presenter double-clicks launch

- **WHEN** the same launch identity is submitted more than once
- **THEN** exactly one root issue is intended
- **AND** each response identifies the same scenario and issue

#### Scenario: another scenario is current

- **WHEN** a different launch is requested before the current scenario is reset
- **THEN** the controller returns the current scenario without creating another issue

### Requirement: Reset SHALL be honest about cancellation limits

Reset SHALL succeed only when the selected scenario's program and attempts are
terminal. It SHALL close the root issue, durably mark the cockpit scenario
reset, and permit a fresh launch. It SHALL refuse an active scenario with a
structured next action and SHALL NOT claim unsupported Host API cancellation.

#### Scenario: presenter resets settled evidence

- **WHEN** the program is terminal and all correlated attempts are terminal
- **THEN** reset is idempotent
- **AND** the next launch may create a fresh scenario

#### Scenario: presenter requests reset during work

- **WHEN** any program or correlated attempt is nonterminal
- **THEN** reset returns conflict
- **AND** preserves the running or waiting workflow unchanged

### Requirement: Presenter documentation SHALL support short and full demos

The integration SHALL publish a step-by-step 3–5 minute route using retained
evidence and a 10–15 minute live route covering launch, clarification, approval,
dependency gates, acceptance, deployment intent, health, and reset.

#### Scenario: presenter chooses the appropriate route

- **WHEN** live preflight is green
- **THEN** the presenter can follow the complete live route without reconstructing hidden setup steps
- **AND WHEN** live preflight is not green or time is bounded
- **THEN** the presenter can use retained accepted evidence without claiming a new live run
