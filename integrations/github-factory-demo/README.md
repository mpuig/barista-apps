# GitHub issue → Factory → verified draft pull request

This integration is a persistent signed-webhook controller. Every accepted
`issues/opened` event launches one ephemeral Factory App Run. Factory resolves
the issue and exact repository base, gives the deterministic worker an isolated
checkout, harvests its patch, integrates it, and runs the seed acceptance check.
The controller then re-verifies Factory's canonical result and patch, creates an
idempotent draft pull request through `GitHubForge`, comments on the issue, saves
a compact result, and deletes the owning session.

The persistent trigger is not an App Run. Factory and each child worker are.
There is no second provider scheduler.

## Authority boundaries

- `BARISTA_GITHUB_WEBHOOK_SECRET` only authenticates ingress.
- `GH_TOKEN` is bootstrap authority and may create/delete the demo repository
  and webhook. Do not keep this broad token in the runtime service.
- `BARISTA_GITHUB_TOKEN` is runtime forge authority. Scope it only to the one
  repository with Issues read/write, Contents read/write, Pull requests
  read/write, and Metadata read.
- `BARISTA_HOST_API_TOKEN` is runtime Factory authority.
- `BARISTA_GITHUB_PROJECT_TOKEN` is optional Projects v2 projection authority.
  Keep it separate from runtime forge authority and scope it to project access.
- GitHub, Projects, and Host API tokens stay in the trusted controller. They are not put in
  App Run envelopes, Factory/worker environments, objective files, argv, clone
  URLs, result documents, or webhook responses.
- The issue title and body are untrusted objective data. They cannot change the
  repository, exact base, worker app, command, acceptance file, branch, delivery
  target, or credentials.

Use a **public disposable repository** for this reference demo. That permits
Factory's Git acquisition without granting a GitHub token to the session.

## Build and publish digest-pinned images

From the repository root:

```sh
docker buildx build --platform linux/amd64,linux/arm64 \
  -f apps/factory/Dockerfile -t REGISTRY/barista-factory:github-demo --push .
docker buildx build --platform linux/amd64,linux/arm64 \
  -f apps/github-issue-triage/Dockerfile \
  -t REGISTRY/barista-github-issue-triage:0.1.0 --push .
docker buildx build --platform linux/amd64,linux/arm64 \
  -f apps/github-issue-worker/Dockerfile \
  -t REGISTRY/barista-github-issue-worker:0.1.0 --push .
# Record registry-reported sha256 index digests. Tags are not executable identity.
```

The Factory image must contain the runtime manifest whose identity is installed
by setup. If you change image references or digests, build from a matching
manifest rather than claiming a different runtime identity.

## Install the controller

```sh
cd integrations/github-factory-demo
uv sync --extra test
```

The Host API configuration uses the normal SDK variables, notably
`BARISTA_HOST_API_ENDPOINT` and `BARISTA_HOST_API_TOKEN`.

## Managed beta deployment

Beta uses `https://github-factory.beta.barista.sh`. DNS and Caddy are managed by
the reviewed `barista-cloud` deployment; this repository deploys controller
source and beta-local workload images:

```sh
bash integrations/github-factory-demo/deploy-beta.sh
```

The command refuses dirty/non-`main` source before SSH, copies additively to both
Hetzner hosts, excludes every `.env` plus SQLite/results state, builds Factory
and the issue worker on the managed node, pushes them only to its loopback
registry, records registry response digests, installs a hardened loopback-only
systemd controller, and starts it only if its separate root-owned environment
file exists. The beta-local images are acceptance infrastructure, not durable
public releases.

Use the keyring-backed broad GitHub bootstrap authority separately to create or
reuse and seed `mpuig/barista-factory-demo`, install the signed webhook, and
install the exact node-reported app identities:

```sh
cd integrations/github-factory-demo
uv run python bootstrap-beta.py
```

The runtime token is different. Create a fine-grained GitHub token selected only
for `mpuig/barista-factory-demo` with:

- Metadata: read
- Contents: read and write
- Issues: read and write
- Pull requests: read and write

Save that token as one line in a mode-0600 file outside the repository. Do not
paste it into argv, a shell assignment, setup state, or an App Run. Provision it
with the existing Host API key and generated webhook secret over SSH stdin:

```sh
uv run python provision-beta.py \
  --repository https://github.com/mpuig/barista-factory-demo \
  --github-token-file /secure/path/github-factory-runtime-token
```

Provisioning atomically writes `/etc/barista/github-factory-demo.env` as
root:root mode 0600, restarts the unit, and verifies both loopback and public
health without printing any credential. Re-running source deployment preserves
this file and `/var/lib/barista-github-factory-demo`.

### Optional GitHub Projects board

GitHub Projects is a presentation-only projection. SQLite controller state
remains authoritative: moving a card cannot advance work, and restart
reconciliation writes the canonical state back to the board. Projection failure
is durable and retryable but does not turn a successful Factory workflow into a
failure.

Use a separate classic token with `read:project` and `project`, or an equivalent
least-scope fine-grained Projects credential. The currently authenticated `gh`
token must have those scopes before live setup. Create a project, or safely
reuse one by explicit number:

```sh
export BARISTA_GITHUB_PROJECT_TOKEN='...project-only token...'
uv run barista-github-demo project-setup \
  --owner OWNER \
  --title 'Barista product program' \
  --public
# Or: --project-number N
```

Setup returns only non-secret project identity and adds missing `Work Type`,
`Program`, `Feature`, `Attempt`, `Dependency`, `Result`, and `PR` fields. The
normal `Status` field is required by projection. Configure the controller with:

```sh
export BARISTA_GITHUB_PROJECT_NUMBER=N
export BARISTA_GITHUB_PROJECT_OWNER=OWNER
export BARISTA_GITHUB_PROJECT_OWNER_KIND=user  # or organization
```

Default state mapping is `accepted/awaiting_input → Todo`, `running → In
Progress`, and terminal states → `Done`. Override display names only with the
closed JSON object `BARISTA_GITHUB_PROJECT_STATUS_OPTIONS`; all six canonical
controller states must remain mapped.

For beta, keep the project token in its own mode-0600 file and add these options
to provisioning:

```sh
uv run python provision-beta.py \
  --repository https://github.com/mpuig/barista-factory-demo \
  --github-token-file /secure/path/github-factory-runtime-token \
  --project-token-file /secure/path/github-project-token \
  --project-number N --project-owner mpuig
```

## Bootstrap

Expose the controller at a stable public HTTPS URL. The webhook URL must end in
`/webhooks/github`. Then use a separate bootstrap token:

```sh
export GH_TOKEN='...bootstrap token...'
export BARISTA_GITHUB_WEBHOOK_SECRET="$(python -c 'import secrets; print(secrets.token_hex(32))')"
export BARISTA_HOST_API_ENDPOINT='https://...'
export BARISTA_HOST_API_TOKEN='...'

uv run barista-github-demo setup \
  --owner OWNER \
  --repository barista-factory-demo \
  --webhook-url https://PUBLIC_HOST/webhooks/github \
  --factory-image REGISTRY/barista-factory:github-demo \
  --factory-digest sha256:... \
  --triage-image REGISTRY/barista-github-issue-triage:0.1.0 \
  --triage-digest sha256:... \
  --worker-image REGISTRY/barista-github-issue-worker:0.2.0 \
  --worker-digest sha256:...
```

Setup creates (or explicitly reuses with `--reuse`) a public repository, refuses
to overwrite differing seed files, installs the digest-pinned Factory and
worker manifests, creates/updates an `issues` and `issue_comment` webhook, and writes mode-0600
`.barista-github-demo.json`. It never writes either token or the signing secret
to that state file.

After setup, replace `GH_TOKEN` with a repository-scoped runtime token:

```sh
unset GH_TOKEN
export BARISTA_GITHUB_TOKEN='...one-repository runtime token...'
export BARISTA_GITHUB_REPOSITORY='https://github.com/OWNER/barista-factory-demo'
export BARISTA_FACTORY_APP='github-demo-factory@0.1.0'
export BARISTA_FACTORY_TRIAGE_APP='github-issue-triage'
export BARISTA_FACTORY_WORKER_APP='github-issue-worker'
# Optional comma-separated trusted responders; defaults to the repository owner.
export BARISTA_GITHUB_AUTHORIZED_RESPONDERS='OWNER'
# Identify the runtime bot so its own marker comments cannot resume work.
export BARISTA_GITHUB_CONTROLLER_LOGIN='barista-factory-bot'
# Optional, separate non-authoritative Projects projection:
# export BARISTA_GITHUB_PROJECT_TOKEN='...project-only token...'
# export BARISTA_GITHUB_PROJECT_NUMBER=1
# export BARISTA_GITHUB_PROJECT_OWNER=OWNER
uv run barista-github-demo serve --host 0.0.0.0 --port 8098
```

Persist the SQLite database and results directory across controller restarts.
The supplied controller image uses `/state` for both. For local testing, put the
bound port behind an HTTPS tunnel such as Cloudflare Tunnel or ngrok; configure
the GitHub webhook with the tunnel's exact `/webhooks/github` URL. Treat tunnel
URLs as setup identity: rerun setup with `--reuse` when the URL changes.

## Replacing the deterministic worker with a coding agent

The reference worker makes the security boundary and acceptance deterministic.
A coding-agent worker can replace it without changing the controller: publish a
digest-pinned, long-running app whose configured command reads only
`BARISTA_OBJECTIVE_PATH`, edits the current isolated checkout, and exits. Give
its model credential directly to that worker app through a separate provider
secret reference; never put the credential, credential value, or secret name in
the issue or App Run input. Keep Factory's repository acquisition, patch bounds,
secret scan, coordinator-owned acceptance, runner-owned delivery, and cleanup
unchanged. Set `BARISTA_FACTORY_WORKER_APP` and the JSON argv in
`BARISTA_FACTORY_WORKER_COMMAND` to the reviewed replacement.

## Run the demo

1. Open a GitHub issue in the disposable repository.
2. GitHub receives `202` after signature, event, action, repository, issue URL,
   and delivery-ID validation; processing continues asynchronously.
3. Inspect `GET /runs/X-GitHub-Delivery` or the issue comment.
4. Review the draft PR. Its body includes a patch digest marker used for retry
   convergence.
5. Confirm the App Run owning session and successful worker sessions are gone.

For an opt-in real-GitHub acceptance against the running public controller:

```sh
uv run barista-github-demo accept \
  --controller-url https://PUBLIC_HOST \
  --clarify \
  --output github-factory-live-evidence.json
```

With `--clarify`, this creates an unclear real issue, verifies the correlated
question comment, posts one authorized answer, requires a fresh attempt, then
verifies the draft flag, exact base/head, patch marker, canonical Factory result
digest, and owning-session absence. It writes non-secret evidence and is
intentionally not part of CI. Omit `--clarify` for the direct-ready path.

Factory/session failures and pre-delivery verification failures are recorded as
`failed` and preserve the owning session for bounded operator forensics. A
controller restart re-dispatches durable `accepted` or `running` rows. A
validated clarification result can end an attempt as `awaiting_input`; only a
signed fresh comment from a configured responder advances it. Duplicate, stale,
bot, and self comments remain inert. Attempt run names are stable
`github-REPO-issue-N-attempt-A` identities while the publication branch remains
`barista/issue-N`. Stable identities plus forge-side marker checks prevent
retry-created duplicate sessions or pull requests.

## Teardown

Delete the webhook while keeping the disposable repository:

```sh
export GH_TOKEN='...bootstrap token...'
uv run barista-github-demo teardown
```

Or explicitly delete both:

```sh
uv run barista-github-demo teardown --delete-repository --yes-really-delete
```

Provider app uninstallation is intentionally separate because existing sessions
or other demos may reference an installed identity.

## Tests

```sh
uv run --extra test pytest -q
cd ../../apps/github-issue-worker && uv run --extra test pytest -q
cd ../factory && uv run --extra test pytest -q
```
