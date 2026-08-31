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

## Preflight profile

Preflight is the no-spend rehearsal gate. It runs every default check, then
materializes each configured immutable agent app, verifies its provider binding
without printing it, checks the pinned CLI version, exercises pause/resume, and
deletes the session. The first run may pull images; run it before—not during—a
presentation.

```sh
export BARISTA_MANAGED_SMOKE_AGENT_CHECKS='[
  {"name":"claude","app":"claude","command":["/bin/sh","-c","test -n \"$ANTHROPIC_API_KEY\" && claude --version"],"expected":"2.1.251"},
  {"name":"pi","app":"pi","command":["/bin/sh","-c","test -n \"$ANTHROPIC_API_KEY\" && pi --version"],"expected":"0.73.1"},
  {"name":"codex","app":"codex","command":["/bin/sh","-c","test -n \"$OPENAI_API_KEY\" && test \"$OPENAI_BASE_URL\" = \"https://eu.api.openai.com/v1\" && codex --version"],"expected":"0.151.0"}
]'
uv run barista-managed-smoke \
  --profile preflight \
  --check-url cloud=https://provider.example/healthz \
  --check-url factory-controller=https://factory.example/healthz \
  --check-url generated-app=https://generated.example/ \
  --output preflight.json
```

The shell expands binding values only inside the provider-created workload. The
configured argv contains environment names and a non-secret regional endpoint,
never credential values. Passing preflight proves image acquisition, binding
presence, version identity, and lifecycle readiness; it is not model-inference
evidence.

## Model profile

Model checks target already-installed portable apps. Replace the preflight
configuration with non-secret inference argv and expected-marker data:

```sh
export BARISTA_MANAGED_SMOKE_AGENT_CHECKS='[
  {"name":"claude","app":"claude","command":["claude","-p","Reply exactly CLAUDE_SMOKE_OK"],"expected":"CLAUDE_SMOKE_OK"},
  {"name":"pi","app":"pi","command":["pi","--print","Reply exactly PI_SMOKE_OK"],"expected":"PI_SMOKE_OK"},
  {"name":"codex","app":"codex","command":["/bin/sh","-c","printf %s \"$OPENAI_API_KEY\" | codex login --with-api-key >/dev/null 2>&1 && exec codex -c \"openai_base_url=$OPENAI_BASE_URL\" exec --skip-git-repo-check --sandbox read-only \"Reply exactly CODEX_SMOKE_OK\""],"expected":"CODEX_SMOKE_OK"}
]'
uv run barista-managed-smoke --profile model --output model-smoke.json
```

Do not put Anthropic or OpenAI credentials in that JSON, argv, or caller
provided environment. The installed manifests name provider-side secret
references; the provider resolves those references into each app workload. A
model check verifies the exact marker, pause/resume, and unconditional cleanup.
Codex 0.151.0 does not treat the API-key environment variable as persisted CLI
login state. The reviewed invocation above moves the provider-injected value to
`codex login` over stdin, suppresses login output, and then replaces the shell;
the value is never placed in argv or the report. The provider also resolves the
EU base URL reference. The invocation expands that non-secret provider value
into Codex's `openai_base_url` configuration because this CLI release does not
honor `OPENAI_BASE_URL` directly. Neither resolved value belongs in the smoke
configuration JSON.

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
partial report. Profiles are explicit: `preflight` never substitutes for paid
`model` evidence, and `model` never substitutes for the real-TTL `slow` gate.
Successful test resources are deleted. Failed Factory evidence
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
the Program 21 public application.

Public multi-architecture Claude 2.1.251, Pi 0.73.1, and Codex 0.151.0 OCI
indexes are available from `ghcr.io/mpuig`; their registry-reported index and
per-platform digests are recorded in the change evidence.

After Cloud sequence 5 enabled generic operator-bound reference resolution,
report `smoke-dd9c8170ee8b4d87b0a189c848b76744` passed the model profile. It
proved the default lifecycle and Factory checks, all three public URLs, exact
Claude/Pi/Codex markers, model-session pause/resume, and unconditional cleanup.
The acceptance tenant had zero sessions afterward. The report was scanned
against the tenant token and provider credentials with no match.
