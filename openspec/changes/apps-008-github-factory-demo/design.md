# Design: GitHub issue-to-Factory demo

## D1. Persistent trigger, ephemeral Factory

A small HTTP controller remains online and responds to GitHub. Each accepted
`issues.opened` event launches one idempotent Factory App Run. Factory workers,
the coordinator, and their compute remain per-issue and are cleaned only after
result and delivery evidence are durable.

The webhook request returns `202` after a durable SQLite claim; it never waits
for repository acquisition or agent work. Pending claims are resumed after a
controller restart.

## D2. Credentials stay outside run content

The controller owns the GitHub token and webhook signing secret. The canonical
App Run contains an explicit `change` delivery with `options.executor="runner"`
but no token value. Factory verifies and emits the integrated patch while
recording the pending declared delivery. The controller retrieves and verifies
that patch from the still-running owning session, invokes `GitHubForge`, records
the exact draft/base/head/result, then cleans the session.

This deliberately avoids passing `GITHUB_TOKEN` through session `env`, argv,
repository URLs, objective content, results, or logs. A future provider secret
resolver may execute the same delivery inside Factory; this controller does not
pretend current providers resolve `secret://` references they do not implement.

## D3. Webhook content is routing input, not authority

The raw body is authenticated with HMAC-SHA256 before JSON parsing. The
controller accepts only `X-GitHub-Event: issues`, action `opened`, and the single
configured repository full name. It derives repository, issue URL, branch,
checks, worker command, app names, and delivery target from trusted
configuration. It then has Factory resolve the issue from GitHub again. Issue
body/title cannot choose commands, references, credentials, egress, or targets.

`X-GitHub-Delivery` is the transport idempotency key. A unique repository/issue
claim additionally prevents two delivery IDs for one opened issue from
launching two runs.

## D4. Deterministic worker for a reproducible demo

The demo worker reads the bounded objective file written by Factory, validates
the issue JSON, and creates `issues/issue-N.md`. It never evaluates issue text
as code. The trusted controller plants a per-issue acceptance script that checks
the exact generated path, title, body, and source URL after integration. This
makes the webhook-to-PR loop demonstrable without a model provider or another
secret. A real coding-agent worker can later replace only the configured worker
app and command.

## D5. Bootstrap is explicit and reversible

`github-factory-demo setup` creates or reuses a named repository, pushes a seed
commit, installs one webhook with the configured signing secret, and installs
caller-supplied digest-pinned Factory/worker manifests. It prints identifiers,
never tokens or the webhook secret. `teardown` removes only the webhook recorded
in local state; repository deletion requires a separate explicit flag.

Bootstrap and runtime tokens may be the same for a disposable demo, but docs
recommend a broad short-lived bootstrap token and a narrower runtime token.

## D6. Result model

SQLite stores webhook receipt, run name, state, canonical Factory result digest,
resolved base, patch digest, draft URL/base/head, and sanitized error. A status
endpoint exposes this non-secret record. On success the controller posts a
GitHub issue comment linking the draft. Publication or collection failure keeps
the Factory session for bounded forensics and reports `failed`; only complete
success triggers cleanup.
