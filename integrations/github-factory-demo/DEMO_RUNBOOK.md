# Presenter runbook: approved product program

This is the human-controlled end-to-end demo. It uses the disposable public
repository, persistent signed controller, ephemeral Factory/worker App Runs,
and the public non-authoritative Project board.

## Open these tabs

- Project: <https://github.com/users/mpuig/projects/4>
- Repository issues: <https://github.com/mpuig/barista-factory-demo/issues>
- Pull requests: <https://github.com/mpuig/barista-factory-demo/pulls>
- Controller health: <https://github-factory.beta.barista.sh/healthz>
- Tenant activity: <https://beta.barista.sh/app/activity>

The Project and Cloud activity stream are projections. SQLite controller state
is authoritative.
Manually moving a card never approves or releases work.

## 1. Start with an incomplete brief

Create a repository issue with:

**Title**

```text
Build a deployment status board
```

**Body**

```text
[barista:product-program]
[barista:needs-input]

Build a small deployment status product that we can run as one container.
```

GitHub should receive `202`. The issue appears in Project #4 while a fresh
Factory run starts isolated triage at an exact `main` commit.

## 2. Clarify, do not resume

Factory posts one bounded clarification question and ends attempt 1. Reply from
the configured `mpuig` responder:

```text
The product must expose health and deployment history, persist events in SQLite,
and serve a responsive browser dashboard. Package the backend and compiled
frontend in one OCI image and one runtime container. Preserve existing issue
records and repository acceptance checks.
```

The signed comment creates attempt 2. No expired session is resumed.

## 3. Approve the BRD as a human

Attempt 2 opens a draft `BRD for product program #N` pull request. Review:

- `docs/brd/program-N.md`
- the exact base/head identities and patch marker
- the one-container, compiled-frontend, SQLite acceptance contract

Mark the PR ready if GitHub requires it, then merge it as `mpuig`. The merge—not
a Project field—is approval. The controller records the approved bytes, digest,
commit, actor, and timestamp.

Canonical status is available at:

```text
https://github-factory.beta.barista.sh/programs/program-N
```

## 4. Watch planning and dependency gates

A least-authority planner reads the exact approved BRD and emits one canonical
acyclic plan:

1. `status-api`
2. `event-store` → depends on `status-api`
3. `dashboard` → depends on `event-store`

The controller independently validates the plan and idempotently creates all
three feature issues. Only `status-api` starts. The other issues remain blocked;
creating or moving their Project cards cannot release them.

## 5. Merge each verified feature

For each draft feature PR:

1. Review its patch and Factory evidence.
2. Mark it ready if needed.
3. Merge it as `mpuig`.
4. Refresh the program endpoint and Project.

Each successor starts from the then-current `main`, only after its predecessor's
correlated merge:

- `status-api` creates the Python service, health API, one-container manifest,
  writable `/data` binding, and Docker skeleton.
- `event-store` adds bounded SQLite-backed `GET/POST /api/events` and tests.
- `dashboard` adds source and compiled frontend assets plus the multi-stage
  one-image Docker build.

## 6. Final acceptance

After `dashboard` merges, Factory resolves current `main` once and runs trusted
acceptance without forge, model, project, or inherited Host API credentials.
It verifies:

- one runtime container and declared writable SQLite binding;
- health and event APIs;
- SQLite persistence tests;
- source and compiled frontend equality;
- backend serving the compiled frontend;
- multi-stage Dockerfile producing one runtime image.

The terminal program status becomes `accepted`. Failed workers, integrity
failures, and acceptance failures remain durable for bounded forensics.

## 7. Review activity and request deployment

Open `/app/activity/program-N` in the authenticated Cloud console. Confirm the
generic stream shows the original source timestamps, exact accepted commit,
BRD/plan/acceptance digests, issue and pull-request links, and an available
`deploy-N` action. Cloud renders these facts without interpreting Factory,
GitHub, BRD, feature, or deployment semantics.

Select **Deploy** and confirm the action. This records durable human intent; it
does not run a Cloud command. The Factory controller polls only its own source
requests, re-reads the authoritative program, requires `accepted`, claims the
request once, and invokes its fixed deployment adapter. The adapter builds the
accepted commit, publishes one digest-pinned image, launches it with its declared
writable binding, and verifies its credential-free HTTPS health endpoint before
the controller settles the request.

Refresh the stream and verify:

- the request progressed `requested → running → succeeded` only through the
  source controller;
- the deployment event is last without changing earlier event timestamps;
- the result names the deployment, session, endpoint, and `sha256` image digest;
- the action is no longer available; and
- a failed terminal request, if demonstrated, remains immutable while retry uses
  a newly versioned action identity such as `deploy-2`.

## Presenter notes

- Emphasize that issues, comments, BRDs, plans, and Project fields are data—not
  command or credential authority.
- Factory may publish verified draft work but cannot approve or merge its own PR.
- The controller holds GitHub delivery and activity-source authority; tokens
  never enter App Runs.
- Cloud stores generic activity and human intent but cannot authorize or execute
  a deployment.
- Feature issues are visible together, while execution remains serial because of
  the approved dependency graph.
- Use a new root issue for each presentation. Stable identities make webhook and
  publication retries converge instead of creating duplicate intended work.
