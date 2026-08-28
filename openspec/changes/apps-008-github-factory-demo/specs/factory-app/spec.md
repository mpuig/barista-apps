## ADDED Requirements

### Requirement: Factory SHALL support explicit runner-owned delivery

When a declared delivery selects trusted runner execution, Factory SHALL perform
all acquisition, integration, independent verification, and patch publication
to the owning session, but SHALL NOT contact the forge or claim the external
delivery completed. Its canonical result SHALL identify the pending delivery so
the runner can verify and fulfill it.

#### Scenario: runner owns GitHub credential

- **WHEN** a software-change run declares draft delivery with
  `options.executor="runner"`
- **THEN** Factory returns a verified integrated patch without receiving a
  GitHub token, and the runner may deliver only after validating that result
