# Evidence

## Offline verification

- Controller integration: `uv run pytest -q` — 67 passed.
- Factory: `uv run pytest -q` — 84 passed; contracts — 74 passed; standalone acceptance — passed; supply-chain check — passed.
- The generated product service manifest was independently validated against the App Manifest v1alpha1 schema.
- Targeted Ruff checks passed for controller source, projection/deployment code, provisioning, and tests.
- Generic mapping tests cover chronological evidence, exact commits and digests, disabled and enabled deployment seams, monotonic corrective revisions, immutable event identities, fixed-adapter I/O, durable request resolution, authority separation, and projection failure isolation.
- `openspec validate apps-012-generic-activity-projection --strict` — valid.

## Security mutation

Removed the accepted-program predicate before source-side deployment and ran:

```text
uv run pytest -q tests/test_activity_projection.py -k cannot_deploy_unaccepted
FAILED (mutation_exit=1)
```

The original predicate was restored. With the unmodified implementation the test proves that a Cloud action request cannot turn an `implementing` program into deployable work or invoke the trusted deployment adapter.

## Managed evidence

- Deployed Apps revision `4cd3f0e9b5c94568ad68505b095086891abd614b` from clean remote `main`; controller health reports generic activity and trusted deployment enabled.
- Registered `software-factory` through Cloud's generic narrow-source API. Its separate token is mode `0600` and differs from forge, Project, Host API, webhook, and deployment credentials.
- Startup reconciliation projected six persisted programs into the authenticated tenant. Program 21 reached activity revision 4 with 17 chronologically ordered events and exact GitHub/Project evidence.
- Program 21 artifacts preserved the independently observed BRD, plan, acceptance-result, acceptance-command, accepted commit, and deployed OCI image identities. The acceptance result remained `sha256:d1339e3547616ac3e02d0a34a6a091868f7e10157441087018fde8050fae4d09`.
- An explicit human request created generic action `deploy-2` / request `ar-a073f368ae5b46a694ec52b23979f9f7`. Cloud recorded intent only. The Apps-owned source runner revalidated accepted commit `a93f1921a44271505196c400abd6afa37b5f5829`, used a restricted forced-command builder identity, and settled the request as succeeded.
- The deployed service session `product-program-21` is running with OCI digest `sha256:1cacdd2794c31653da4b8fee1a5a845ca25553af03827a4e2692890bef28f302` at `https://factory-program-21.at.beta.barista.sh`; `/api/health` returned `status: ok`.
- The first deployment request remains terminal failed evidence: it correctly refused to claim success when the configured public suffix disagreed with the returned endpoint. After the external suffix policy was corrected, a fresh human action identity retried rather than mutating the terminal result.
- Full-width and 390px responsive console captures backed by the live tenant API are in `artifacts/program-21-activity.png` and `artifacts/program-21-activity-mobile.png`. The timeline includes clarification, BRD proposal/approval, plan, every feature issue/PR/merge, exact acceptance, and deployment; provenance includes repository, Project, commits, digests, and generated endpoint.
