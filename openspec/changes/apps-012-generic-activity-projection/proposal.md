# Proposal: Generic activity projection from applications

## Why

Factory's durable program state is useful after ephemeral App Runs are cleaned, but users currently reconstruct it from GitHub, a presentation-only Project, and a controller JSON endpoint. Cloud should not learn Factory or GitHub semantics. Applications instead need a generic way to project durable activity, evidence links, artifacts, and human-requestable actions into a tenant's console.

## What changes

- Add an optional generic activity publisher to the GitHub Factory demo controller.
- Translate controller-owned program state into the provider's generic activity-stream envelope.
- Publish through a separate tenant activity credential with stable stream, event, link, artifact, and action identities.
- Keep the controller database authoritative and make projection corrective and non-blocking.

## What does not change

- Activity state cannot approve, merge, release dependencies, accept, or deploy a program.
- Cloud fields and action requests are untrusted external projection state.
- GitHub, Project, Host API, and activity credentials remain separate.
- Factory remains portable when the optional activity API is absent.
