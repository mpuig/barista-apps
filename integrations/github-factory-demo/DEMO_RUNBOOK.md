# Presenter runbook: approved product program

This is the human-controlled demo for the disposable public repository,
persistent signed controller, ephemeral Factory/worker App Runs, and public
non-authoritative Project board. Use the short route when reliability and
explanation matter most; use the live route when the audience needs to see each
human boundary happen.

## Before the audience arrives

1. From `barista-apps`, run the no-spend command in
   [`docs/managed-acceptance.md`](../../docs/managed-acceptance.md) with
   `--profile preflight` and the three public URLs. Require a green report.
2. Confirm the managed tenant inventory is empty after preflight.
3. Open <https://github-factory.beta.barista.sh/presenter>. It should say
   **Live**, show controller state, and reveal no credential.
4. Open the Project, repository pull requests, and authenticated Cloud Activity
   in adjacent tabs. Sign in manually; never automate passwords, passkeys, 2FA,
   PATs, or OAuth codes.
5. To use **Launch scenario** or **Reset**, select **Unlock controls** and enter
   the separate mode-`0600` presenter token manually. It remains in that browser
   tab's session storage and is not part of the cockpit state.

If preflight is not green, do not start a live run. Use the retained-evidence
route below instead.

## 3–5 minute route: explain retained evidence

Use this route for a dependable product tour with no model spend during the
presentation.

1. **Open the cockpit.** Select `program-21` under **Recent programs**. Point out
   the six-stage rail: Brief, BRD approval, Plan, Features, Acceptance, Deploy.
2. **Show the dependency workbench.** Explain that all three planned features
   are visible, but each successor was released only after its dependency's
   exact correlated merge.
3. **Show exact evidence.** Call out the BRD, plan, accepted commit, deployment
   state, and immutable image identity. These come from controller SQLite, not
   the Project board.
4. **Open Cloud Activity.** Show the same work as generic events and artifacts.
   Cloud does not interpret BRDs, features, GitHub, or Factory policy.
5. **Open the deployed product.** Visit
   <https://factory-program-21.at.beta.barista.sh/>. If it is paused, the browser
   shows wake progress rather than a raw 409 and then serves the application.
6. **Close on authority.** Factory can prepare work but cannot approve its own
   pull requests; Cloud records deployment intent but cannot execute source
   deployment; disposable sessions are gone.

## 10–15 minute route: run it live

Keep the cockpit as the audience's primary screen. GitHub is where the presenter
performs human decisions; Cloud Activity is where deployment intent is recorded.
Warmed images normally fit this route, but never rush or bypass an approval to
meet the clock.

### Open alongside the cockpit

- Project: <https://github.com/users/mpuig/projects/4>
- Repository issues: <https://github.com/mpuig/barista-factory-demo/issues>
- Pull requests: <https://github.com/mpuig/barista-factory-demo/pulls>
- Tenant activity: <https://beta.barista.sh/app/activity>

The Project and Cloud activity stream are projections. SQLite controller state
is authoritative. Manually moving a card never approves or releases work.

## 1. Launch one reviewed scenario

In the unlocked cockpit select **Launch scenario** once. The controller creates
or reuses one issue with the fixed deployment-status title, incomplete brief,
and inert scenario marker. Double-clicks, retries, and page reloads converge on
the current durable scenario rather than intending another root issue.

The cockpit moves to **Brief** and links the exact issue while a fresh Factory
run starts isolated triage at an exact `main` commit. Do not manually create a
second root issue.

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

## 8. Reset only after settlement

Return to the cockpit after deployment succeeds (or after the program honestly
settles as failed/refused). Select **Reset** and confirm. Reset closes the root
issue and clears the cockpit's current-scenario slot while preserving programs,
events, pull requests, deployment provenance, and bounded failure evidence.
Verify the cockpit returns to **Ready for a clean run**.

Reset deliberately refuses active or waiting work. The current Host API does not
provide a bounded generic cancellation guarantee, so the controller will not
pretend that clearing a screen canceled an execution. Follow the cockpit's next
action until the program settles, or switch to the retained-evidence route.

## Recovery table

| What you see | What it means | Safe response |
| --- | --- | --- |
| Launch returns the current scenario | A click/retry already intended work | Continue with the shown issue; do not create another |
| Brief asks for input | The bounded attempt ended, not paused | Post the reviewed clarification once |
| A feature says blocked | Its approved dependency has not merged | Do not move the Project card to force it |
| Reset is disabled or returns active | Work is not terminal | Complete the displayed human action or retain failure evidence |
| A public app is waking | The session was paused | Leave the page open; bounded automatic retry will settle |
| Deployment action failed | Immutable intent settled as failed | Inspect evidence; use the newly versioned retry action |
| Cockpit says disconnected | Read-state polling failed | Keep GitHub tabs open, check controller health, and use retained evidence |

After any route, finish with controller, Cloud, and deployed-product health
checks and verify zero disposable sessions for the managed acceptance tenant.

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
