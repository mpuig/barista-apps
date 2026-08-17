## 1. Repository and contract foundations

- [x] 1.1 Establish the monorepo package boundaries under `contracts/`, `sdks/`, `providers/local/`, `conformance/`, and `apps/` with independent version metadata.
- [x] 1.2 Define and validate the `v1alpha1` App Manifest JSON Schema, canonical examples, media type, capability/permission vocabulary, and digest rules.
- [x] 1.3 Define the `v1alpha1` Host API OpenAPI contract plus SSE cursor and WebSocket attach frame schemas, standard errors, profiles, and extension namespace.
- [x] 1.4 Define canonical Session Story and semantic-state bundle schemas, media types, digest rules, and redaction-policy versioning.
- [x] 1.5 Add compatibility/golden tests for deterministic schemas, manifests, stories, errors, and content identities.

## 2. Provider conformance

- [x] 2.1 Build the black-box conformance runner with provider endpoint/credential configuration and versioned reports.
- [x] 2.2 Add core-profile cases for discovery, manifest rejection, ensure/lifecycle, idempotency, exec/attach, events/cursors, artifacts, errors, and cleanup.
- [ ] 2.3 Add independent profiles for exact snapshot, fork, capsule, delegated grants, Story publication, and branch evaluation.
- [x] 2.4 Add a mandatory standalone harness that blocks Cloud DNS/endpoints, removes Cloud credentials, and detects proprietary imports or network attempts.
- [x] 2.5 Publish machine-readable conformance results whose skips cannot satisfy an advertised profile.

## 3. Local Host API provider

- [x] 3.1 Implement the core Host API over a configured loopback/Unix-socket Barista Node Agent with local durable metadata and artifact storage.
- [x] 3.2 Add local OS-user authority, user-owned socket defaults, explicit authenticated remote opt-in, and no multi-tenant claims.
- [x] 3.3 Translate actual Node/runtime/configuration capabilities into Host API profiles and standard errors.
- [x] 3.4 Implement local app install/validate, session handles, idempotent operations, event cursor persistence, artifact retention, and restart recovery.
- [ ] 3.5 Add optional local fork/capsule/grant profiles only after the pinned kernel passes their integration tests.
- [x] 3.6 Pass the core conformance and restart/recovery tests with Barista Cloud blocked.

## 4. SDK and adapter framework

- [x] 4.1 Generate/build the first Python SDK with endpoint selection, auth source, capability negotiation, typed errors, idempotency, operation waiting, streams, and attach helpers.
- [x] 4.2 Add the provider-neutral adapter interface for detect, semantic export, continuation launch, capability/fidelity report, and result collection.
- [x] 4.3 Add common sensitive-data handling so manifests, logs, stories, and semantic bundles accept references/redactions but reject raw declared secrets.
- [x] 4.4 Test the same SDK/app code against local and Cloud conformance endpoints using configuration changes only.

## 5. Reference agent adapters

- [x] 5.1 Create independent `apps/pi`, `apps/claude`, and `apps/codex` packages with manifests, digest-pinned images, supported-version declarations, and auth references.
- [x] 5.2 Implement Pi native-state detection/export/continuation and a fidelity report without provider-specific fields.
- [x] 5.3 Implement Claude Code native-state detection/export/continuation and a fidelity report without provider-specific fields.
- [x] 5.4 Implement Codex native-state detection/export/continuation and a fidelity report without provider-specific fields.
- [x] 5.5 Add fixture-based round trips that preserve native opaque attachments and refuse unsupported versions loudly.

## 6. Factory app

- [x] 6.1 Port the coordinator, mission schema, status, receipts, notifications, and worker lifecycle into `apps/factory` using only Host API/SDK calls.
- [x] 6.2 Replace tenant API key inheritance with mission-scoped delegated grants and enforce repository, adapter, secret, egress, concurrency, attempt, deadline, and budget limits.
- [x] 6.3 Implement idempotent ensure/recovery so coordinator restart never duplicates an accepted worker attempt.
- [x] 6.4 Register receipts/artifacts before successful worker deletion and preserve failed-worker or failed-capsule forensics according to mission policy.
- [x] 6.5 Pass one deterministic multi-worker mission locally with Cloud blocked and the same mission against the Cloud provider.

## 7. Lift app

- [x] 7.1 Implement source classification and an explicit `exact`, `semantic`, or `auto-with-confirmation` mode with no silent semantic fallback from exact.
- [ ] 7.2 Implement exact capsule export/import/compatibility/target acceptance while preserving the source until acceptance.
- [x] 7.3 Implement semantic export/import through adapters with workspace, VCS, transcript, skills, tools, environment, continuation prompt, and missing-component inventory.
- [x] 7.4 Emit transfer receipts and resumable failed-transfer state without secret values.
- [x] 7.5 Test exact compatible transfer, exact incompatibility, native macOS semantic transfer, interrupted upload, target rejection, and source preservation.

## 8. Session Story app

- [ ] 8.1 Implement selection and canonical assembly of events, decisions, commands, diffs, commits, receipts, evaluations, and explicit artifacts.
- [ ] 8.2 Implement versioned deterministic redaction, high-confidence secret blocking, unknown-media failure, and removal-category reporting.
- [ ] 8.3 Ensure story bundles contain no capsule objects, writable filesystem, bearer grants, or executable permission and verify content provenance/signatures.
- [ ] 8.4 Add a standalone static story viewer/export path and golden tests for determinism, redaction, pseudonymization, and non-executability.

## 9. Documentation, security, and release

- [ ] 9.1 Document the kernel/provider/app boundary, third-party provider guide, manifest authoring, capability profiles, and local standalone installation.
- [ ] 9.2 Publish a threat model covering untrusted apps, delegated grants, exact-memory secrets, malicious capsules, stories, adapters, and local remote-binding hazards.
- [ ] 9.3 Add supply-chain checks for pinned images, schema/package signatures, generated-client drift, dependency audit, and app image provenance.
- [ ] 9.4 Run `openspec validate apps-001-portable-agent-apps --strict`, schema/golden/unit tests, and every advertised provider profile.
- [ ] 9.5 Run the full mandatory conformance suite and Pi + Factory + semantic Lift + Story acceptance flow with Barista Cloud DNS blocked, no Cloud credential, and no proprietary package available.

