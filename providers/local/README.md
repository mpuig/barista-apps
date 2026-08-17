# providers/local

The single-user reference **Host API provider** over a local `barista.sh` Node
Agent. It exists so apps work with no managed service and no proprietary
component — a fresh, offline install is genuinely useful.

- **OpenSpec:** `openspec/changes/apps-001-portable-agent-apps/specs/local-host-provider/spec.md`

## Run it

```bash
cd providers/local

# Default: a user-owned Unix socket under the data dir (not remotely reachable).
uv run barista-local-provider

# Local tooling over loopback TCP:
uv run barista-local-provider --host 127.0.0.1 --port 8088

# Against a real Node Agent (needs the `grpc` extra + kernel's barista-proto):
uv run barista-local-provider --node-grpc 127.0.0.1:50051
```

By default it binds a Unix socket owned by the current user and authorizes the
local OS user. A non-loopback bind requires `--allow-remote` **and** `--token`.
It never claims tenant isolation, billing, global placement, or public sharing.

## What it does

- Implements the Host API **core profile** — discovery, app install/validate,
  ensure/get/list/delete, pause/resume, exec, events (resumable cursors),
  artifacts, operations.
- **Honest capabilities.** It advertises a profile only when the underlying
  node reports it. The default in-memory fake node has disk-level lifecycle and
  pause/resume but no exact memory snapshot or fork, so the provider advertises
  `core` + `session.pause_resume` and nothing more.
- **Durable + recoverable.** Metadata, apps, sessions, the event journal, and
  artifact records live in SQLite + files under the data dir (default
  `~/.local/share/barista-local-provider`). They survive a restart with the
  same logical identifiers and are readable without this binary.

## Node backends

- **`FakeNodeClient`** (default) — in-memory, persisted to the data dir. No
  hypervisor; ideal for offline development and CI.
- **`GrpcNodeClient`** (`grpc` extra) — talks Contract A to a real Node Agent.
  Requires the kernel's generated `barista-proto` package (from
  `github.com/mpuig/barista.sh`, `py/barista-proto`), which is not on PyPI.

## Tests

```bash
cd providers/local && uv run --extra test pytest -q
```

The headline test runs the apps-001 §2 conformance suite against this provider
over real HTTP **with Barista Cloud blocked**, proving it is a conformant
provider. Others cover restart recovery, single-user auth, honest capability
translation, and manifest rejection.
