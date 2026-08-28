# Design

## Deployment split

The control plane owns DNS and Caddy for
`github-factory.beta.barista.sh`. This repository owns controller source,
systemd, persistent state, and Factory/worker image builds. Keeping those
responsibilities separate avoids teaching the proprietary control plane how to
build open app workloads while ensuring future control-plane deploys preserve
the public route.

## Source and image identity

Deployment refuses a dirty checkout or a revision different from remote `main`
before contacting either host. Source is copied additively with `.env`, virtual
environments, caches, databases, and result state excluded. The managed node
builds and pushes single-platform images into its loopback registry, then reads
the registry's `Docker-Content-Digest`; a local Docker image ID or mutable tag is
never recorded as executable identity.

The images remain beta-local acceptance infrastructure. Their references are
valid only for the one node where `127.0.0.1:5000` resolves to the registry.

## Controller service

The control-plane host creates an unprivileged `barista` service with
`ProtectSystem=strict`, a private temporary directory, and write access only to
`/var/lib/barista-github-factory-demo`. It reads
`/etc/barista/github-factory-demo.env`, which deployment neither creates nor
reads. If the file is absent, deployment installs the unit but does not start
it. A separate stdin-only provisioning step supplies runtime secrets after the
operator provides the repository-scoped GitHub token.

## Failure semantics

Image build/push, package install, service restart, and health verification are
fatal. The deployed-source marker is written only after image digest discovery
and, when configured, controller health. Existing service state, secret files,
and destination-only files are never deleted.
