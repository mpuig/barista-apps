## Purpose

Moves an agent session between local and remote hosts using exact execution transfer when compatible and honest semantic continuation otherwise.

## ADDED Requirements

### Requirement: Lift SHALL distinguish exact and semantic modes

Lift SHALL classify the source before transfer. Exact mode SHALL require a
Barista-managed compatible snapshot/capsule and preserve memory, process, disk,
and lineage. Semantic mode SHALL start a new process from an adapter bundle and
report transferred and missing components. Lift SHALL never silently substitute
semantic mode for a requested exact transfer.

#### Scenario: exact-only request fails on native process
- **WHEN** a user requests exact Lift for a native process not managed by Barista
- **THEN** Lift refuses with an explanation and does not claim or perform semantic continuation

### Requirement: Lift SHALL preserve the source until target acceptance

Lift SHALL export and verify the transfer, import it at the target, validate
compatibility, start or prepare the target, and require an acceptance condition
before optionally pausing or deleting the source. Failure SHALL leave the source
usable and record recoverable transfer state.

#### Scenario: target import failure leaves laptop session intact
- **WHEN** upload succeeds but target verification fails
- **THEN** the source remains available and Lift reports the failed target without deleting source state

### Requirement: Lift SHALL produce a transfer receipt

Every Lift SHALL record source/target provider, mode, content and lineage ids,
adapter and versions, compatibility results, redactions, transferred/missing
components, target acceptance, and source disposition without secret values.

#### Scenario: user can audit semantic fidelity
- **WHEN** semantic Lift completes
- **THEN** its receipt states exactly which transcript, workspace, skills, and configuration components were transferred or omitted

