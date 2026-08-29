# factory-app delta

## ADDED Requirements

### Requirement: typed product-program operations

The Factory SHALL support closed typed `product-brief`, `feature-plan`, and `program-acceptance` operations while retaining existing mission delivery and issue SDLC behavior.

#### Scenario: product brief needs clarification

- **WHEN** the bounded brief lacks a required product decision
- **THEN** the operation returns one canonical bounded question
- **AND** does not start planning or implementation

#### Scenario: feature plan is invalid

- **WHEN** a planner returns duplicate identities, unknown dependencies, a cycle, or out-of-scope delivery
- **THEN** the Factory records an integrity failure
- **AND** returns no feature-delivery request

#### Scenario: final acceptance runs

- **WHEN** the exact assembled commit is supplied after dependency completion
- **THEN** a least-authority acceptance worker runs deterministic trusted checks
- **AND** the Factory returns a canonical verified program result
