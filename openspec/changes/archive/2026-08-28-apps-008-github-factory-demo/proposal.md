# Change: GitHub issue-to-Factory demo

## Why

Portable repository runs now acquire exact Git state, verify integrated changes,
and deliver draft pull requests, but there is no event ingress that turns a
GitHub issue webhook into a run. A user cannot yet demonstrate the complete
repeatable loop—create issue, receive webhook, run ephemeral Factory, publish a
verified draft, clean compute—without writing a bespoke controller and unsafe
credential plumbing.

## What Changes

- Add an OSS GitHub webhook controller with signature verification, repository
  scoping, durable delivery deduplication, asynchronous Factory launch, verified
  result/patch collection, explicit runner-owned draft delivery, and cleanup.
- Add a bootstrap CLI that creates or selects a demo repository, seeds a bounded
  deterministic project, installs a webhook, and installs digest-pinned Factory
  and worker manifests.
- Add a deterministic demo worker that turns issue objective content into a
  reviewable repository change without executing issue text.
- Allow Factory's declared draft delivery to be explicitly fulfilled by its
  trusted runner after independent verification, so the GitHub token stays in
  the webhook controller rather than entering the App Run or worker sessions.
- Add offline webhook/GitHub/provider acceptance and an opt-in real GitHub demo.

## Impact

This adds one controller package and one worker app. It does not add webhook,
GitHub, run, or secret vocabulary to the Host API. Providers continue to see an
opaque canonical App Run compiled to existing app/session/event/artifact
resources. The controller is the delivery authority and retains the GitHub
credential; Factory and workers receive objective content, never that token.
