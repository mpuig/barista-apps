# Evidence

## Pre-deployment validation

- GitHub Factory controller/deployment tests: **25 passed**
- New-package Ruff: **passed**
- Standalone acceptance: **1 passed, 3 skipped, 1 deselected**
- Supply-chain check: **OK**
- Strict Apps OpenSpec: **4/4 active changes valid**
- `bash -n integrations/github-factory-demo/deploy-beta.sh`: **passed**

Deployment has not yet run. Live tasks remain unchecked until both deployment
changes are merged and the exact remote-main revisions are installed. Runtime
GitHub token provisioning and real issue acceptance intentionally wait for the
user-supplied repository-scoped token.
