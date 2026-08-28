## 1. Deployment tooling

- [x] 1.1 Add clean-remote-main preflight and additive CP/node source transfer.
- [x] 1.2 Build and push Factory and worker images on the managed node and record registry digests.
- [x] 1.3 Install the controller package, hardened systemd unit, persistent state, and conditional health gate.
- [x] 1.4 Add stdin-only secret provisioning that never places tokens in argv or source state.

## 2. Safety and documentation

- [x] 2.1 Add deployment-hygiene tests for source identity, `.env` exclusion, no deletion, registry digest use, service hardening, and secret separation.
- [x] 2.2 Document the beta hostname, exact token permissions, provisioning, setup, health, acceptance, recovery, and teardown.
- [x] 2.3 Run package tests, standalone acceptance, supply-chain checks, and strict OpenSpec.

## 3. Live deployment

- [ ] 3.1 Merge reviewed Apps and Cloud deployment changes before production mutation.
- [ ] 3.2 Deploy images, controller source, DNS, and Caddy from clean remote `main` revisions.
- [ ] 3.3 Provision the user-supplied repository-scoped token, bootstrap the disposable repository, and verify controller health.
- [ ] 3.4 Run the real issue-to-draft acceptance, record exact evidence, and confirm all ephemeral sessions are gone.
