# app-sdk Specification

## Purpose

Gives applications a small, provider-neutral client layer for negotiation, lifecycle, streams, idempotency, and harness adapters.

## Requirements

### Requirement: Provider selection SHALL be configuration-only

The SDK SHALL select a Host API endpoint and credential source through explicit
configuration. App business logic SHALL not branch on `local` versus `cloud`;
it MAY branch only on discovered capabilities or namespaced extensions.

#### Scenario: endpoint switch needs no code change
- **WHEN** a user changes the endpoint from a local socket adapter to Barista Cloud
- **THEN** the same app command runs without importing another provider client

### Requirement: SDK mutations SHALL be safely retryable

The SDK SHALL generate or accept stable idempotency keys, expose operation
handles, wait with bounded timeouts, and resume event observation from cursors.
It SHALL not retry terminal or authorization errors as transient failures.

#### Scenario: network loss does not duplicate a worker
- **WHEN** the create response is lost and the SDK retries
- **THEN** it reuses the idempotency key and returns the original worker session

### Requirement: Adapter interfaces SHALL preserve opaque harness state

The SDK SHALL define adapter operations for detect, export semantic state,
build a continuation launch, report capabilities, and collect a result. The
generic interface SHALL treat harness-native transcript/session formats as
opaque attachments with media types and SHALL not normalize away information.

#### Scenario: Pi-specific state round-trips opaquely
- **WHEN** the Pi adapter exports and later imports its native session attachment
- **THEN** the attachment bytes and declared media type are preserved by the SDK and provider

