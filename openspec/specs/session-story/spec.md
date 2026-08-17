# session-story Specification

## Purpose

Creates a deterministic, redacted, non-executable account of session knowledge that can be shared independently from terminal or capsule access.

## Requirements

### Requirement: A Session Story SHALL contain only declared knowledge records

A story bundle SHALL contain a versioned manifest and selected prompts/events,
decisions, commands, patches, commits, receipts, evaluations, and artifacts by
digest. It SHALL contain no memory snapshot, writable filesystem, bearer grant,
secret value, or automatic executable capability.

#### Scenario: story import cannot create a live clone
- **WHEN** a reader imports a story bundle without a separately granted capsule
- **THEN** the reader can inspect its records but cannot restore, attach, exec, or fork the source session

### Requirement: Redaction SHALL be deterministic and fail closed

Story generation SHALL apply a versioned redaction policy, preserve a record of
removed fields by category, and produce stable output for the same inputs and
policy. Unknown required media types or unresolved high-confidence secrets SHALL
block a publishable result.

#### Scenario: same policy produces same public bundle
- **WHEN** the same selected records are processed twice with the same redaction-policy version
- **THEN** the resulting canonical story manifest and content digests are identical

### Requirement: Provenance SHALL remain verifiable without leaking private names

Stories SHALL identify source content, adapter, app, and receipt digests and MAY
carry signatures. A provider MAY replace tenant-private names with stable public
pseudonyms, but SHALL not alter signed content without producing a new story id.

#### Scenario: public pseudonym preserves integrity
- **WHEN** a Cloud provider hides a private session name for publication
- **THEN** it publishes a newly identified story envelope whose included record digests remain verifiable

