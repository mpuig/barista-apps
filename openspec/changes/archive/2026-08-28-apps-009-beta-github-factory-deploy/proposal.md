# apps-009: Beta deployment for the GitHub Factory demo

## Why

The signed webhook controller and deterministic worker are merged and proven
offline, but the real acceptance cannot run until the controller has stable
public ingress and the managed node can pull exact Factory and worker images.
Those operator steps must be repeatable without turning beta-local images into a
claim of public release availability or placing credentials in source control.

## What changes

- Add an additive, clean-main-only beta deploy command for controller source and
  the two node-local workload images.
- Install the controller as a hardened systemd service with a separate
  root-owned environment file and persistent SQLite/result state.
- Record registry-reported workload digests and deployed source revision without
  reading, copying, or deleting production environment files.
- Add preflight and deployment-hygiene tests plus an operator handoff for secret
  provisioning and post-deploy health checks.

## Portability

This is optional beta operations tooling. The controller, worker, App Run
contract, local provider tests, and offline fake-forge acceptance continue to
work with Barista Cloud absent. No beta hostname, Hetzner behavior, or loopback
registry enters an App Run or provider-neutral contract.
