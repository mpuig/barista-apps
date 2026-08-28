## ADDED Requirements

### Requirement: An App Run SHALL have a canonical provider-neutral envelope

An App Run SHALL identify an app, a declared operation, a stable run name,
app-specific input with a media type, named external bindings, reference-only
secrets, and requested deliveries as separate fields. Binding and delivery kinds
SHALL be namespaced identifiers whose app-specific meaning a provider need not
understand.

#### Scenario: the same run is portable between providers

- **WHEN** a caller submits the same canonical run envelope to two conformant providers that satisfy the app's declared capabilities
- **THEN** both providers launch the same app operation with the same inputs and bindings without adding provider-specific fields

#### Scenario: a convenience flag does not create a second contract

- **WHEN** a CLI accepts a convenience flag such as `--repo`
- **THEN** it maps the flag to a binding in the canonical run envelope before launch

### Requirement: Run inputs SHALL be validated before side effects

A runner SHALL validate the run envelope and every manifest-declared constraint
it can evaluate before installing an app, creating a session, acquiring a
binding, or publishing a delivery. An undeclared operation, binding name,
binding kind, or delivery kind SHALL be refused without those side effects.

#### Scenario: undeclared delivery is refused before launch

- **WHEN** a run requests a draft-pull-request delivery that its app operation did not declare
- **THEN** validation fails and no session, branch, or pull request is created

#### Scenario: invalid embedded input is refused before launch

- **WHEN** an operation embeds an input schema and the run input does not satisfy it
- **THEN** validation fails before app installation or session creation

### Requirement: A run SHALL carry secret references and never secret values

Run credentials SHALL be named references whose values are resolved through the
app manifest and provider authority path. The canonical run envelope, logs,
results, receipts, and delivery metadata SHALL NOT contain the resolved secret
value.

#### Scenario: private repository authority is referenced

- **WHEN** a run binds a private repository using a declared credential
- **THEN** the run contains a `secret://` reference or its local alias and the resolved credential is delivered separately

#### Scenario: plaintext forge credential is refused

- **WHEN** a caller supplies a plaintext token where a run secret reference is required
- **THEN** validation fails before repository or forge access

### Requirement: App source SHALL resolve to immutable executable identity

A remote app source SHALL be resolved at an exact source revision to a valid App
Manifest before launch. The workload identified by the manifest SHALL remain
digest-pinned. App source revision, manifest identity, and workload digest SHALL
be recorded in the result.

#### Scenario: an app repository is not executed directly

- **WHEN** a caller selects an app by Git repository URL
- **THEN** the resolver selects an exact revision and validates its manifest rather than executing an unpinned repository command

#### Scenario: mutable workload identity is refused

- **WHEN** the resolved app manifest names only a mutable workload tag
- **THEN** the run fails before a session is created

### Requirement: Bindings SHALL resolve once and preserve provenance

A binding adapter SHALL resolve a mutable external reference once, record its
immutable identity when the resource supports one, and expose that resolved
identity to the app result. An app SHALL receive only binding kinds and names
its selected operation declared.

#### Scenario: a Git branch is pinned for the run

- **WHEN** a run binds a Git repository branch
- **THEN** the branch is resolved to one commit before work begins and every worker receives a workspace based on that commit

#### Scenario: a moving branch does not change an in-flight run

- **WHEN** the bound branch advances after resolution
- **THEN** the run continues against the previously recorded commit

### Requirement: Objective content SHALL not widen run authority

Text obtained through an objective binding, including a forge issue, SHALL be
untrusted app input. It SHALL NOT alter declared secrets, egress, resource
budgets, repository scope, binding scope, acceptance policy, or delivery target.

#### Scenario: issue text requests an unrelated publication

- **WHEN** an issue instructs the app to push to a repository outside the declared workspace binding
- **THEN** the instruction is treated as content and the publication is refused

### Requirement: External delivery SHALL be explicit

A run SHALL perform an external side effect only when the selected app operation
declared that delivery kind and the run explicitly requested it. A failed
independent check SHALL prevent publication by default.

#### Scenario: repository input alone does not publish

- **WHEN** a run binds a writable repository but requests no delivery
- **THEN** it may return a patch or artifact but creates no branch or pull request

#### Scenario: verified Factory run creates a draft pull request

- **WHEN** a Factory software-change run explicitly requests draft-pull-request delivery and its independent integration checks pass
- **THEN** it publishes only the verified integrated head and returns the draft pull request reference

#### Scenario: failed verification stays unpublished

- **WHEN** the independent integration check fails and no explicit failed-draft policy exists
- **THEN** no pull request is created and the result exposes the failed evidence and recoverable local output

### Requirement: Run lifecycle SHALL determine completion

Each selected app operation SHALL declare one lifecycle from `job`, `service`,
`interactive`, or `coordinator`. A runner SHALL use that lifecycle to decide
whether to wait for a terminal result, readiness and an endpoint, readiness and
an attachable session, or a coordinator's terminal result after child evidence
is collected.

#### Scenario: service readiness is not job completion

- **WHEN** a service operation reaches readiness
- **THEN** the runner returns its endpoint as ready without requiring the service session to terminate

#### Scenario: coordinator completes after evidence collection

- **WHEN** all child work is terminal but the coordinator has not registered its result and receipts
- **THEN** the coordinator run is not yet reported complete

### Requirement: A terminal run SHALL produce a verifiable canonical result

A job or coordinator SHALL write a canonical result in its owning session and
register its digest before that session may be deleted. The runner SHALL collect
the bytes while the owning session exists, verify the digest, and persist any
requested local output before cleanup. The result SHALL identify the run,
operation, exact app/workload identity, resolved bindings, outcome, evidence,
and outputs without credential values.

#### Scenario: result is collected before cleanup

- **WHEN** a job succeeds and cleanup is enabled
- **THEN** the runner verifies and persists the registered result before deleting the owning session

#### Scenario: digest mismatch is not reported as success

- **WHEN** collected result bytes do not match the registered digest
- **THEN** the runner reports a result-integrity failure and does not claim the run succeeded

### Requirement: App Runs SHALL work without Barista Cloud

The App Run contract, validation, local app resolution, local Git binding,
result collection, and fake-forge delivery SHALL be implementable and testable
against the local Host API provider with Cloud DNS, credentials, and proprietary
packages absent.

#### Scenario: standalone software-change run

- **WHEN** a caller runs the reference software-change workflow against a local Git repository and offline forge double
- **THEN** it resolves, runs, verifies, and returns the delivery reference without contacting Barista Cloud or a public network
