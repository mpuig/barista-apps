## Why

The portable contract can install a digest-pinned app and ensure a session for
it, but it does not define what it means for a user to **run an app**. The only
launch inputs today are untyped `args`, non-secret `env`, and opaque metadata.
Nothing declares which inputs an app accepts, which external resources are
bound to a run, whether the workload is a terminating job or a lasting service,
or what result the caller can retrieve.

Factory makes the omission obvious: a useful invocation needs an app, a project
repository, an objective such as an issue, a bounded workflow, and an explicit
delivery such as a draft pull request. Encoding all of that as Factory-only CLI
flags would solve the demonstration and leave Pi, Lift, Story, evaluators, data
jobs, and services with the same missing contract.

The nearest open change is `apps-001-portable-agent-apps`: it introduced the App
Manifest, SDK, and reference apps, and its task 6.2 says Factory enforces
repository bounds. It does **not** define an app-run envelope or a repository
binding, and the current Factory mission schema has no repository field.
`apps-002-child-session-authority` concerns delegated authority after an app has
launched and is orthogonal. A focused follow-up is required rather than
stretching either umbrella change after the fact.

## What Changes

- Define a versioned, provider-neutral **App Run** envelope that identifies the
  app operation, app-specific input, named external bindings, secret references,
  requested deliveries, and a stable run name.
- Let an App Manifest declare named run operations, their lifecycle (`job`,
  `service`, `interactive`, or `coordinator`), accepted input media type, binding
  kinds, and delivery kinds. Existing manifests remain valid and retain their
  session-launch meaning.
- Define bindings as named, typed references. A Git repository, forge issue,
  Barista session, dataset, or artifact is a binding; none becomes a special
  Host API field.
- Make external side effects explicit deliveries. A draft pull request is one
  delivery kind, not the universal result of running an app.
- Add SDK support that validates a run before side effects, resolves an app to a
  manifest whose workload is immutable by digest, launches one owning session
  through the existing Host API, and retrieves a canonical result from that
  session.
- Prove the model with at least two different app shapes: a single-agent
  repository job and Factory's coordinating software-change workflow. Keep
  Story/Lift/service examples as contract fixtures so the shape cannot quietly
  become repository-only.
- Add a portable runner CLI. Managed-provider CLIs may expose it as
  `barista run`; the OSS implementation and tests remain usable with Barista
  Cloud absent.

## Capabilities

### New Capabilities

- `app-runs`: typed app operations, run envelopes, external bindings, explicit
  deliveries, lifecycle, and canonical results over the portable Host API.

### Modified Capabilities

- `app-manifest`: manifests may declare named run operations and their accepted
  inputs, bindings, lifecycle, and deliveries.
- `app-sdk`: the SDK can validate, launch, observe, and collect a portable app
  run without provider-specific code.
- `host-api`: an authorized runner can retrieve an installed app's validated
  manifest in order to select and validate a run operation by app name.
- `factory-app`: Factory accepts a repository workspace and objective through
  the shared run contract and may explicitly deliver a verified draft pull
  request.

## Impact

- **Contracts:** a new `contracts/app-run/v1alpha1` JSON Schema, an additive
  `runs` declaration in the App Manifest schema, and read access to an installed
  app's validated manifest through the existing Host API app resource.
- **SDK:** new immutable App Run models, validation, canonical serialization,
  and a runner that compiles to existing install/ensure/exec/events/artifact
  operations rather than introducing a second scheduler.
- **Apps:** Pi and Factory gain declared run operations. Factory keeps
  `$BARISTA_FACTORY_MISSION` as its canonical in-session mission delivery
  mechanism; the shared runner maps the validated Factory input to it.
- **Source control:** Git acquisition, issue reading, and pull-request
  publication live in portable source-control/forge adapters. The Host API
  preserves typed envelopes and enforces grants; it does not learn GitHub
  semantics.
- **Security:** app repositories are locators, not executable identity. A remote
  app source must resolve to a manifest and digest-pinned workload before any
  session is created. Issue text is untrusted objective content and cannot widen
  secrets, egress, budgets, repositories, or publication targets. Deliveries
  with external side effects require an explicit request and reference-only
  credentials.
- **Standalone:** schemas, validation, Git behavior, and publication behavior
  are tested against local repositories and an offline fake forge. No Cloud
  account, proprietary package, or public network is required.
- **Compatibility:** existing manifests and direct `ensure_session(args, env)`
  callers continue unchanged. Apps without a run declaration remain launchable
  as sessions but are not presented as typed `app run` operations.
