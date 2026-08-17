# Standalone install (no Barista Cloud)

A fresh, offline install is genuinely useful: install `barista.sh` and this
repository on one machine and run portable apps with Barista Cloud unreachable.

## 1. Run a local provider

```bash
cd providers/local
uv run barista-local-provider                 # user-owned Unix socket (default)
# or, for local tooling:
uv run barista-local-provider --host 127.0.0.1 --port 8088
```

By default it binds a socket owned by your user and authorizes the local OS
user. It advertises only what the underlying node supports; with the built-in
fake node backend that is `core` + `session.pause_resume`. Point it at a real
Node Agent with `--node-grpc <addr>` (needs the `grpc` extra + the kernel's
`barista-proto`).

## 2. Drive it with the SDK

```python
from barista_app_sdk import BaristaClient, Config

with BaristaClient(Config(endpoint="http://127.0.0.1:8088")) as client:
    client.negotiate(required=[])
    client.install_app(manifest)
    s = client.ensure_session(manifest["name"], name="work")
    h = client.exec(s.id, ["echo", "hi"])
    client.wait_operation(h.operation_id)
```

## 3. Run apps offline

- **Factory:** `cd apps/factory && uv run barista-factory run missions/example.json --endpoint http://127.0.0.1:8088`
- **Story:** `cd apps/story && uv run barista-story build records.json --created-at <ts> --out story.json --html story.html`
- **Adapters (Pi/Claude/Codex) and Lift** are libraries used by apps and the SDK.

## 4. Prove it is offline

```bash
cd conformance
BARISTA_CONFORMANCE_STANDALONE=1 \
BARISTA_HOST_API_ENDPOINT=http://127.0.0.1:8088 \
uv run barista-conformance
```

The standalone profile installs a process-wide guard that fails the run if
anything resolves Barista Cloud DNS, connects to a Cloud address, or imports a
proprietary module. The repository's own acceptance flow
(`acceptance/`) runs the whole stack — provider, conformance, Factory, semantic
Lift with the Pi adapter, and Story — under that guard.
