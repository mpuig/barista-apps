# local-host-provider Specification

## Purpose

Supplies a useful, single-user OSS Host API provider over barista.sh so applications work with no managed service or proprietary component.

## Requirements

### Requirement: Local provider SHALL run without Barista Cloud

The local provider SHALL start against a loopback or Unix-socket Node Agent,
use local durable metadata and artifact storage, and require no Cloud account,
Cloud API key, proprietary package, external database, or public DNS. It SHALL
implement the Host API core profile.

#### Scenario: fresh offline installation is useful
- **WHEN** a user installs barista.sh and barista-apps on one machine with Cloud unreachable
- **THEN** the user can install an app, ensure a session, exec, attach, observe events, store an artifact, pause, resume, and delete it

### Requirement: Local authority SHALL be explicit and single-user

By default the provider SHALL bind only to loopback or a user-owned socket and
authorize the local OS user. Remote binding SHALL require explicit authenticated
configuration. The provider SHALL NOT claim tenant isolation, billing, global
placement, or public sharing.

#### Scenario: default listener is not remotely reachable
- **WHEN** the local provider starts with default configuration
- **THEN** another host cannot connect to its Host API port or socket

### Requirement: Local capabilities SHALL derive from actual kernel support

The provider SHALL translate discovered Node Agent/runtime capabilities and
configured local services into Host API profiles. It SHALL not advertise remote
capsules without a configured compatible store or exact fork without kernel
support.

#### Scenario: missing object store is reported
- **WHEN** the kernel supports local snapshots but no capsule object store is configured
- **THEN** local snapshot capability is reported and remote capsule portability is not

### Requirement: Local data SHALL be exportable and recoverable

Provider metadata, app manifests, stories, and artifacts SHALL use documented
formats and paths and SHALL survive provider restart. Removing the provider
binary SHALL not make capsule or artifact bytes proprietary or unreadable.

#### Scenario: restart preserves app state
- **WHEN** the local provider restarts after registering an app and artifact
- **THEN** both remain addressable with the same logical identifiers

