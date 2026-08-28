# Proposal: resumable issue-driven SDLC Factory

## Why

The GitHub Factory demo proves signed ingress, inert issue objectives, isolated workers, independent acceptance, verified patch delivery, and cleanup. Its deterministic worker always produces one markdown file; it does not perform the normal engineering loop of checking clarity, asking questions, implementing, testing, and stopping safely when evidence is insufficient.

A long-lived paused Factory session is the wrong way to wait for a human: delegated grants expire while paused and the wait can last days. Human input should end one ephemeral attempt and trigger a new, correlated attempt from durable controller state.

## What changes

- Add a typed Factory `issue-sdlc` operation with triage, implementation, and independent verification stages.
- Define a bounded triage-decision protocol: `ready`, `needs_input`, or `refused`.
- Return independently verifiable question and failure outputs without granting the Factory GitHub authority.
- Extend the GitHub controller to accept scoped `issue_comment.created` answers, authorize responders, persist attempt lineage, and launch a fresh ephemeral attempt.
- On `needs_input` or a recoverable verification failure, publish an idempotent issue question and stop mutation.
- On success, publish the existing digest-identified draft PR and a “Factory verified” evidence comment/check; final GitHub approval remains human or separately authorized policy.
- Keep model credentials scoped to triage/implementation workers and GitHub credentials scoped to the controller.

## What does not change

- Issue and comment content remain untrusted inert data.
- A comment cannot change repository, base, commands, checks, credentials, delivery kind, or responder policy.
- Factory does not approve or merge its own pull request.
- The deterministic reference worker remains available for boundary-only acceptance.
- No provider-specific scheduler or waiting primitive is introduced.

## Acceptance

- Offline end-to-end tests cover unclear → question → authorized answer → new attempt → implementation → independent tests → draft PR.
- Unauthorized comments, duplicate deliveries, stale answers, failed checks, malicious text, and moving bases cannot publish code.
- A failed/unclear attempt leaves no live session after its canonical evidence and question are durably collected.
- Standalone acceptance remains green with Cloud blocked.
