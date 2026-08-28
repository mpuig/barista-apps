# github-factory-deployment Specification

## Purpose
TBD - created by archiving change apps-009-beta-github-factory-deploy. Update Purpose after archive.
## Requirements
### Requirement: Beta GitHub Factory deployment SHALL preserve exact source and workload identity

The deployment SHALL refuse source other than clean remote `main`, copy source
additively without production environment or state deletion, and record
registry-reported Factory and worker workload digests together with the exact
source revision.

#### Scenario: a checkout is dirty or not remote main

- **WHEN** an operator invokes the beta deployment from that checkout
- **THEN** deployment stops before contacting the control-plane or node hosts

#### Scenario: workload images are published to beta

- **WHEN** Factory and worker images build and push successfully
- **THEN** the recorded executable identities are the registry response digests, not local image IDs or tags

### Requirement: Controller deployment SHALL keep runtime secrets out of source and deployment transport

The controller SHALL run unprivileged with persistent bounded state and obtain
its Host API token, repository-scoped GitHub token, and webhook secret from a
separate root-owned environment file that ordinary source deployment neither
reads nor writes.

#### Scenario: runtime secrets are not provisioned yet

- **WHEN** controller source and the systemd unit are deployed without the environment file
- **THEN** deployment leaves the unit installed but does not start a broken or unauthenticated controller

#### Scenario: deployment is repeated

- **WHEN** destination-only state, result, or environment files already exist
- **THEN** source synchronization preserves them and a configured healthy controller restarts on the reviewed source

