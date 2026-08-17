# provider-conformance Specification

## Purpose

Provides black-box evidence that independent Host API implementations preserve portable app semantics and advertise only behavior they actually support.

## Requirements

### Requirement: Every provider SHALL pass the core profile

The conformance suite SHALL test discovery, manifest rejection, session ensure
and lifecycle, idempotency, exec exit/streams, attach modes, events, artifacts,
standard errors, and cleanup through only the published Host API. Passing SHALL
require no private provider hooks.

#### Scenario: provider cannot special-case the test
- **WHEN** the suite runs against a configured endpoint and ordinary credentials
- **THEN** it proves behavior solely through public requests and observable resources

### Requirement: Advertised optional profiles SHALL have matching tests

Fork, capsule, grants, publication, and evaluation profiles SHALL each have
independent tests. A provider SHALL fail conformance if it advertises a profile
whose semantics it cannot pass.

#### Scenario: fake fork claim fails
- **WHEN** a provider advertises exact fork but cold-boots the child
- **THEN** the fork profile fails because in-memory source state is absent

### Requirement: A Cloud-absent standalone profile SHALL be mandatory

The repository's local provider and every first-party OSS app SHALL pass an
integration profile with network access to Barista Cloud disabled and without
a Cloud credential, proprietary package, DNS name, or service.

#### Scenario: Factory runs offline from Cloud
- **WHEN** the standalone test launches Factory against a local Barista host with Cloud blocked
- **THEN** Factory completes its reference mission and harvests results without attempting a Cloud endpoint

### Requirement: Results SHALL identify contract and provider versions

A conformance report SHALL record schema version, suite version, provider name
and version, advertised profiles, passed/failed/skipped cases, and environment
constraints. A skip SHALL never count as passing an advertised requirement.

#### Scenario: optional unsupported profile is an honest skip
- **WHEN** a local provider does not advertise public sharing
- **THEN** sharing tests are reported unsupported/skipped and core conformance may still pass

