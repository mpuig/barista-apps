## ADDED Requirements

### Requirement: Optional generic activity projection

The controller SHALL optionally project its authoritative durable program state into a bounded generic activity-stream API using a separate tenant credential.

#### Scenario: Accepted program is projected
- **WHEN** a program reaches accepted state and generic activity projection is configured
- **THEN** the controller publishes a succeeded activity stream with stable events, evidence links, and exact artifacts
- **AND** the controller database remains workflow authority

#### Scenario: Activity API is unavailable
- **WHEN** projection fails or the generic activity API is unavailable
- **THEN** the controller records a bounded retryable projection error
- **AND** the program state and GitHub workflow do not change

### Requirement: Separate activity authority

The controller SHALL keep activity projection credentials separate from forge, Project, Host API, webhook, and application runtime credentials.

#### Scenario: Activity token equals another controller authority
- **WHEN** configuration supplies an activity token equal to a forge, Project, or Host API token
- **THEN** the controller refuses startup

### Requirement: Source-owned action declaration

The controller SHALL advertise generic human actions only from trusted controller policy and SHALL treat external activity fields and requests as unable to approve, merge, release, accept, or execute program work.

#### Scenario: No deployment runner exists
- **WHEN** an accepted program is projected without a configured trusted deployment runner
- **THEN** its Deploy action is visibly unavailable
- **AND** no external side effect occurs

#### Scenario: Human requests deployment
- **WHEN** a human requests the available Deploy action for an accepted program
- **THEN** the controller durably claims the request by its stable identity
- **AND** independently revalidates accepted program state before invoking one fixed trusted adapter
- **AND** publishes the verified digest-pinned image and HTTPS endpoint as source-owned result evidence

#### Scenario: Controller restarts during result handoff
- **WHEN** deployment completed durably but source resolution was interrupted
- **THEN** the controller resolves the same request from durable state without changing operation identity
