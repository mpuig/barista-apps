# agent-adapters Specification

## Purpose

Provides replaceable integrations for coding-agent harnesses while keeping their installation, state, and continuation semantics out of the Host API.

## Requirements

### Requirement: Each adapter SHALL declare its own contract

The Pi, Claude Code, and Codex adapters SHALL each declare supported versions,
installation or image requirements, authentication references, working-tree
expectations, native state media types, semantic export support, continuation
command, and known limitations. No adapter SHALL require provider code changes.

#### Scenario: unsupported harness version fails early
- **WHEN** an adapter detects a native state version it does not support
- **THEN** it refuses semantic export with a specific adapter compatibility error

### Requirement: Authentication SHALL use references, not manifest values

Adapters SHALL request named grants or secret references and SHALL never write
raw model-provider credentials into app manifests, stories, command logs, or
portable semantic bundles. If only an ambient native credential is available,
the adapter SHALL mark the export sensitive and require explicit user consent.

#### Scenario: exported story omits model API key
- **WHEN** an agent ran with a referenced provider credential
- **THEN** the adapter's story and semantic manifest contain the reference name or redaction, not the credential value

### Requirement: Semantic continuation SHALL be explicit about fidelity

An adapter SHALL report which components it exported—workspace, diff, commits,
transcript, harness session, skills, tool configuration, environment manifest,
and continuation prompt—and which could not be transferred. It SHALL not label a
restarted process as exact continuation.

#### Scenario: native macOS Pi becomes semantic Lift
- **WHEN** Pi runs as a native macOS process outside a compatible Barista runtime
- **THEN** the adapter emits a semantic bundle with a fidelity report rather than claiming process-memory teleportation

