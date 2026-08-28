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
A fresh live issue remains required after deploying this correction.
