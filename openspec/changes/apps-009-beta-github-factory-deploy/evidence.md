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

Runtime GitHub token provisioning and real issue acceptance intentionally wait
for the user-supplied repository-scoped token.
