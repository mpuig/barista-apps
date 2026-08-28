## 1. Contracts and Factory

- [x] 1.1 Add explicit runner-owned delivery semantics to Factory without weakening delivery declaration or verification.
- [x] 1.2 Return pending delivery identity and keep integrated patch bytes retrievable until runner completion.
- [x] 1.3 Add dishonest cases for missing declaration, failed result, wrong digest, moving base, and duplicate delivery.

## 2. Deterministic demo worker

- [x] 2.1 Add a digest-pinned worker manifest, package, Dockerfile, and objective-to-markdown implementation.
- [x] 2.2 Treat issue content only as bounded inert data and refuse malformed/non-issue objectives.
- [x] 2.3 Add worker tests and supply-chain/runtime-manifest checks.

## 3. Webhook controller

- [x] 3.1 Add signature verification, event/action/repository scoping, prompt 202 response, and bounded request parsing.
- [x] 3.2 Add durable SQLite delivery/issue deduplication and restart recovery.
- [x] 3.3 Compile one canonical Factory run per issue from trusted configuration.
- [x] 3.4 Collect canonical result and integrated patch with independent size/digest verification.
- [x] 3.5 Deliver a draft through `GitHubForge`, comment the issue, persist exact result/base/head evidence, and clean only on success.
- [x] 3.6 Expose health and non-secret run-status endpoints.

## 4. Bootstrap and operations

- [x] 4.1 Add setup for repository creation/reuse, deterministic seed push, webhook installation, and digest-pinned app installation.
- [x] 4.2 Add explicit teardown with state-scoped webhook removal and separately confirmed repository deletion.
- [x] 4.3 Document token permissions, webhook ingress, local tunnel/deployment, repeated issues, failure recovery, and replacement with an agent worker.

## 5. Acceptance

- [x] 5.1 Add offline fake GitHub and Host API end-to-end acceptance from signed webhook through draft result and cleanup.
- [x] 5.2 Prove invalid signatures, unsupported events, duplicate deliveries, malicious issue text, failed checks, and publication refusal are inert/safe.
- [x] 5.3 Run affected package tests, standalone acceptance, supply-chain checks, and strict OpenSpec.
- [x] 5.4 Run an opt-in disposable real-GitHub issue-to-draft demo and record exact evidence before archiving.
