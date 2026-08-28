# Evidence

## Pre-deployment validation

- GitHub Factory controller/deployment tests: **25 passed**
- New-package Ruff: **passed**
- Standalone acceptance: **1 passed, 3 skipped, 1 deselected**
- Supply-chain check: **OK**
- Strict Apps OpenSpec: **4/4 active changes valid**
- `bash -n integrations/github-factory-demo/deploy-beta.sh`: **passed**

During live bootstrap, GitHub returned 409 for Git Database blob creation in an
empty repository. GitHub does not expose Git Database operations until the
first commit exists. The bootstrap now creates the first README through the
Contents API, normalizes a non-`main` account default branch if necessary, and
then verifies/creates the remaining exact seed files. A focused empty-repository
test pins that sequence and excludes the failing blob path.

The repository-scoped runtime token was provisioned over SSH stdin and the
controller became healthy through both loopback and public HTTPS. The first
real issue was accepted as run `github-0853c04f40-issue-1`, but the Factory
workload exited before orchestration because its owning session received the
provider-injected token without the provider endpoint. The controller reported
`app_run.result_timeout` after 1800 seconds and preserved the failed session.
Managed-node evidence showed the more specific startup error:
`BARISTA_HOST_API_ENDPOINT is required`.

The controller now passes its Barista client's non-secret provider endpoint in
the owning-session environment while leaving bearer-token injection exclusively
to the provider grant. Offline acceptance asserts this exact authority split.
The failed session was removed only after the managed-node cause and bounded
controller failure were recorded.

## Deployed revisions and identities

- Apps deployment changes: PR #41, plus live fixes #42 and #43
- Deployed Apps revision: `8a3792ee42da42d716530ff25f8228cc4b670c36`
- Cloud ingress changes: PR #190, plus zone-relative DNS fix #191
- Deployed Cloud revision: `73013e2024520b4aeca92b3222787692a5f5868f`
- Public ingress: `https://github-factory.beta.barista.sh`
- DNS target: `46.225.59.43`
- Controller upstream: loopback-only `127.0.0.1:8098`
- Factory workload: `sha256:6c751702cd8a7c10d5cfea03eb04e4e21532a253df33bc30adf631de4793287c`
- Worker workload: `sha256:e1af0377df9da8deee1e88452c92e39ac7836cdf4def4187d1e98fbce1ff7212`
- Disposable repository: `https://github.com/mpuig/barista-factory-demo`
- Webhook ID: `671511064`

The runtime token was read from a mode-0600 local file and provisioned through
SSH stdin. The bootstrap, runtime forge, webhook-signing, Host API, and workload
authorities remained separate. No secret value was printed, placed in argv,
written into setup state, or added to a repository.

## Successful live acceptance

- Issue: `https://github.com/mpuig/barista-factory-demo/issues/2`
- Stable run: `github-0853c04f40-issue-2`
- Base commit: `42155f201a0da9db0e969d8cf9eb2907465337c5`
- Factory result: `sha256:17e6ecc33dc46f56de359289f2409cf9f887cee396bfdd8116e6641937833d96`
- Verified patch: `sha256:2fc5417361957e3ddb392b555e68e90fd2bf7543a304e473518e5773db83409d`
- Draft PR: `https://github.com/mpuig/barista-factory-demo/pull/3`
- Head commit: `d7329557d97487093c33b14f81525b74d8c1865b`
- Issue comment: `https://github.com/mpuig/barista-factory-demo/issues/2#issuecomment-5453527866`
- Owning and child sessions after verified delivery: absent

The PR remained draft, targeted `main`, contained the exact patch-digest marker,
and changed only `issues/issue-2.md`. Controller durable status, GitHub PR/issue
state, repository content, and the Host API session list were checked
independently after the acceptance command completed. Public health remained
HTTP 200 for the allowlisted repository.
