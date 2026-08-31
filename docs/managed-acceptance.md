# Managed demo smoke gate

`barista-managed-smoke` turns the opt-in managed acceptance cases into one
machine-readable release gate. It is separate from the mandatory offline
standalone flow: this command requires network access, a provider endpoint, and
an ordinary tenant credential.

## Default profile

From `acceptance/`:

```sh
export BARISTA_HOST_API_ENDPOINT=https://provider.example
export BARISTA_HOST_API_TOKEN_FILE=$HOME/.config/barista/managed-token
export BARISTA_HOST_API_TOKEN="$(cat "$BARISTA_HOST_API_TOKEN_FILE")"
export BARISTA_FACTORY_COORDINATOR_IMAGE=registry.example/factory:reviewed
export BARISTA_FACTORY_COORDINATOR_DIGEST=sha256:...
export BARISTA_FACTORY_WORKER_IMAGE=registry.example/factory-worker:reviewed
export BARISTA_FACTORY_WORKER_DIGEST=sha256:...

uv run barista-managed-smoke \
  --check-url cloud=https://provider.example/healthz \
  --check-url factory-controller=https://factory.example/healthz \
  --check-url generated-app=https://generated.example/ \
  --output smoke-report.json
```

The default profile runs:

1. guest readiness, exec, explicit pause/resume, filesystem continuity, and
   cleanup;
2. a producer task, dependency transfer, independently checked consumer, and
   durable Factory receipts;
3. each explicitly named public HTTP check.

A selected pytest case that skips is a **failed gate**, even though its direct
developer invocation exits zero. In particular, the credential must address a
tenant whose discovery document advertises `grants.delegated`; otherwise the
Factory step is not evidence.

The image references are parameters because pull authority and immutable image
identity belong to the provider under test. Never replace their digests with
mutable tags.

## Model profile

Model checks target already-installed portable apps. Configure non-secret app,
argv, and expected-marker data:

```sh
export BARISTA_MANAGED_SMOKE_AGENT_CHECKS='[
  {"name":"claude","app":"claude","command":["claude","-p","Reply exactly CLAUDE_SMOKE_OK"],"expected":"CLAUDE_SMOKE_OK"},
  {"name":"pi","app":"pi","command":["pi","--print","Reply exactly PI_SMOKE_OK"],"expected":"PI_SMOKE_OK"},
  {"name":"codex","app":"codex","command":["codex","exec","Reply exactly CODEX_SMOKE_OK"],"expected":"CODEX_SMOKE_OK"}
]'
uv run barista-managed-smoke --profile model --output model-smoke.json
```

Do not put Anthropic or OpenAI credentials in that JSON, argv, or caller
provided environment. The installed manifests name provider-side secret
references; the provider resolves those references into each app workload. A
model check verifies the exact marker, pause/resume, and unconditional cleanup.
Regional Codex installations may configure the EU OpenAI base URL inside the
app/template; that endpoint is not a credential and still must not be supplied
as a secret channel through the smoke command.

## Slow profile

```sh
uv run barista-managed-smoke --profile slow --timeout 1200
```

This retains the unmodified delegated-grant lifetime and proves renewal under
real elapsed time. It is intentionally excluded from the default deployment
loop.

## Report semantics

Reports use `schema_version: v1alpha1` and contain a unique run id, profile,
start/end timestamps, terminal state, and ordered steps. Captured pytest output
is tail-bounded to 8 KiB. HTTP checks record status and sampled byte count; an
HTTP 200 proves reachability, not product semantics.

The command stops on the first failure and exits nonzero after writing the
partial report. Successful test resources are deleted. Failed Factory evidence
must follow the provider's bounded forensic-retention policy; the smoke format
does not invent a provider-specific retention API.

## Current managed evidence

The first run of the new command proved lifecycle continuity and all configured
public URLs, but its Factory step skipped because the appliance-acceptance
tenant does not advertise `grants.delegated`. The command initially translated
that skip into green; its skip-to-failure regression test now prevents that.

A later run used a distinct managed-release-acceptance tenant advertising
`grants.delegated` and `session.pause_resume`. Report
`smoke-f580adf1d65b4e2894e4ba442a1ca9b3` passed lifecycle continuity, the fresh
dependency-gated Factory mission, Cloud health, Factory-controller health, and
the Program 21 public application. The model profile remains pending immutable
published agent images with provider-side secret references.
