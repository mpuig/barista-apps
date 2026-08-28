## 1. Contract shape

- [x] 1.1 Add `contracts/app-run/v1alpha1/schema.json` for the canonical run envelope, named bindings, reference-only secrets, explicit deliveries, and lifecycle-aware canonical results; include valid and invalid fixtures.
- [x] 1.2 Add the optional `runs` map and run-operation definitions to the App Manifest schema without invalidating frozen pre-change manifests.
- [x] 1.3 Add manifest examples covering a job, service, interactive app, and coordinator so repository/PR concepts cannot become required accidentally.
- [x] 1.4 Add golden tests for canonical serialization, undeclared binding/delivery refusal, plaintext credential refusal, lifecycle values, and backward compatibility.
- [x] 1.5 Document the line between app source, project bindings, and digest-pinned executable identity.

## 2. SDK models and validation

- [x] 2.1 Add immutable SDK models for `AppRun`, `RunBinding`, `SecretReference`, `DeliveryRequest`, `RunOperation`, and `AppRunResult` with canonical JSON serialization.
- [x] 2.2 Validate an envelope against its selected manifest operation before any transport call, including embedded input schemas when present.
- [x] 2.3 Add read access to an installed app's validated manifest in the Host API contract, local provider, SDK, and conformance suite; resolved secret values must never appear.
- [ ] 2.4 Add typed app resolution for an installed app and a local manifest source; record exact source revision, manifest identity, and workload digest.
- [ ] 2.5 Compile a validated run to one idempotently ensured owning session using only the existing Host API and canonical `BARISTA_APP_RUN` launch configuration. *(Launch compilation, stable content-derived keys, exact envelope delivery, and Factory bootstrap mapping are implemented; lifecycle observation remains.)*
- [ ] 2.6 Collect the canonical result from the owning session, verify its registered digest, persist requested output, and preserve the session on collection failure.

## 3. Portable runner CLI

- [ ] 3.1 Add an OSS runner command that accepts `--app`, `--input`, repeatable `--bind`, `--output`, and cleanup/detach controls and emits the canonical envelope it runs.
- [ ] 3.2 Add convenience projections for repository, issue, mission, and delivery without creating a second wire shape.
- [ ] 3.3 Resolve a remote app repository only through an exact revision and valid digest-pinned manifest; require an explicit development mode for building app source.
- [ ] 3.4 Keep the runner provider-neutral so a managed CLI can expose the same behavior as `barista run` without the OSS package importing managed code.

## 4. Source and forge adapters

- [ ] 4.1 Implement the Git repository binding with one-time commit resolution, provenance, reference-only credentials, explicit submodule/LFS behavior, and provider-derived size limits where available.
- [ ] 4.2 Implement objective bindings for local text/specification and forge issue content; keep objective content unable to widen run policy.
- [ ] 4.3 Implement patch/branch output and explicit draft-pull-request delivery behind a source-control/forge adapter rather than the Host API.
- [ ] 4.4 Add an offline fake forge and dishonest cases: raw token, moving base ref, publication outside the bound repository, and publication after failed verification.

## 5. Reference app adoption

- [ ] 5.1 Declare and implement a single-agent repository job using the shared Git binding and canonical patch/result output.
- [ ] 5.2 Declare Factory's `software-change` coordinator operation with repository and objective bindings plus patch and draft-pull-request deliveries.
- [x] 5.3 Map validated Factory mission input to `$BARISTA_FACTORY_MISSION`, preserving explicit-path errors and existing direct invocation.
- [ ] 5.4 Give isolated Factory workers equivalent clean bases, collect declared patches, integrate them in the coordinator scope, and run coordinator-owned checks against the integrated tree.
- [ ] 5.5 Publish only a verified integrated head when draft-pull-request delivery was explicitly requested; otherwise return local artifacts and receipts.
- [ ] 5.6 Add Story, Lift, and service manifest fixtures/declarations sufficient to prove the run contract supports non-repository jobs and non-terminal lifecycles.

## 6. Conformance and acceptance

- [ ] 6.1 Add black-box cases proving invalid runs fail before installation/session side effects, retries preserve one owning session, and app-specific fields remain opaque to providers.
- [ ] 6.2 Run the same single-agent and Factory envelopes against the local provider and managed provider by changing endpoint and credential only.
- [ ] 6.3 Run standalone acceptance with Cloud DNS blocked, no Cloud credential, no proprietary import, local Git repositories, and the offline forge.
- [ ] 6.4 Run every affected package test, `scripts/supply_chain_check.py`, and `openspec validate --all --strict`.
- [ ] 6.5 Record the actual command, app/workload identity, resolved project commit, checks, result digest, and draft-PR fake/managed delivery evidence before marking the change complete.

## Reconciliation note

`apps-001-portable-agent-apps` is the closest open change, but it defines
installation/session portability rather than App Runs. Its task 6.2 currently
claims repository bounds that the implemented Factory mission schema cannot
express. This change carries that missing outcome explicitly; completion of
`apps-001` must not be read as evidence that repository acquisition or
publication already exists.

## Not in this change

- A provider-side scheduler or `/runs` state machine separate from sessions.
- Provider-specific GitHub fields in the Host API.
- Automatic publication merely because a writable repository was bound.
- Executing arbitrary commands from an app source repository instead of its
  validated digest-pinned manifest.
- Replacing Factory's existing `$BARISTA_FACTORY_MISSION` bootstrap contract.
