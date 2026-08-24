# sdks/python

The provider-neutral **Barista App SDK** for Python. Write an app once; run it
against a local provider or Barista Cloud by changing only the endpoint and
credential — never a branch on provider name.

- **OpenSpec:** `openspec/changes/apps-001-portable-agent-apps/specs/app-sdk/spec.md`

## Use it

```python
from barista_app_sdk import BaristaClient, Config

with BaristaClient(Config(endpoint="http://localhost:8088")) as client:
    client.negotiate(required=["session.pause_resume"])   # fail fast if unmet
    app = client.install_app(manifest)
    session = client.ensure_session(app["name"], name="work")   # idempotent
    handle = client.exec(session.id, ["echo", "hi"])
    client.wait_operation(handle.operation_id)
    for event in client.events(session.id, max_events=10):
        ...
    client.delete_session(session.id)
```

Switch to Barista Cloud with only configuration:

```python
BaristaClient(Config(endpoint="https://api.barista.sh", token_env="BARISTA_TOKEN"))
```

## What it gives you

- **Config-only provider selection.** `Config(endpoint, token/token_env)`; app
  logic branches only on discovered capabilities, never on `local`/`cloud`.
- **Capability negotiation.** `negotiate(required=[...])` fails before any side
  effect when a provider is missing a required profile; `supports(cap)` for
  optional branching.
- **Typed errors.** `errors.CapabilityError`, `AuthorizationError`,
  `UnavailableError` (retryable), `TerminalError`, … mapped from the contract's
  error classes.
- **Safe retries.** Mutations carry a stable idempotency key; a lost response is
  retried with the same key and never duplicates. Terminal/authorization/
  capability errors are never retried as transient.
- **Operations & streams.** `wait_operation(...)` with bounded timeout;
  `events(session_id, cursor=...)` resumes from a cursor.
- **Delegated grant refresh.** `refresh_grant()` (requires `grants.delegated`)
  replaces the delegated grant the client authenticates with and starts
  presenting the replacement, returning the `Grant` — same resource, same
  actions, later expiry. The credential is the subject, so there is nothing to
  pass and nothing that could widen the result. It is **never retried**: the
  operation takes no idempotency key, so a blind retry would rotate again from a
  secret that no longer works. Rotation has no overlap window, so a request that
  raced one is retried with the new credential rather than raising a spurious
  authentication error.
- **Attach helpers.** `attach.AttachFrame` codec plus `open_attach()` (requires
  the `ws` extra) for byte-clean or PTY attach.
- **Adapters.** `adapters.Adapter` protocol (detect, export semantic state,
  continuation launch, capabilities, collect result) with opaque, content-typed
  native `Attachment`s and an honest `FidelityReport`.
- **Sensitive-data handling.** `sensitive.assert_no_secret_values(...)` rejects
  raw declared secrets in manifests/logs/stories/bundles; `redact_text(...)` is
  deterministic.

## Tests

```bash
cd sdks/python && uv run --extra test pytest -q
```

The headline test runs one unchanged app workflow against both a real local
provider (over HTTP) and a cloud-shaped provider (different identity and
capabilities) — proving portability by configuration alone, fully offline.
