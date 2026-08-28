## 1. Triage protocol and worker boundary

- [x] 1.1 Add a closed bounded triage decision schema and canonical digest model.
- [ ] 1.2 Add Factory child-session execution and collection for triage decisions with separate worker authority.
- [x] 1.3 Prove malformed, oversized, secret-bearing, and policy-shaped decisions are refused.

## 2. Factory SDLC operation

- [ ] 2.1 Add the typed `issue-sdlc` manifest operation and validate its triage, implementation, acceptance, and delivery declarations.
- [ ] 2.2 Stop before implementation on `needs_input` or `refused` and return canonical evidence.
- [ ] 2.3 Feed validated ready context into existing isolated software-change implementation and coordinator-owned acceptance.
- [ ] 2.4 Produce independently verifiable sanitized failure questions only for recoverable implementation/check failures.
- [ ] 2.5 Preserve integrity/authority failures without any delivery request.

## 3. Persistent GitHub conversation

- [x] 3.1 Extend webhook validation for scoped `issue_comment.created` without weakening exact-byte signature checks.
- [x] 3.2 Persist issue workflow status, attempt number, prior result, question digest, answer comment identity, and delivery deduplication.
- [x] 3.3 Authorize responders from trusted configuration and ignore bots, self-comments, stale comments, and duplicates.
- [ ] 3.4 Verify and post idempotent question/failure comments; launch a new ephemeral attempt after one authorized answer.
- [x] 3.5 Keep stable branch identity while giving each attempt a stable unique run identity.

## 4. Delivery and verification

- [ ] 4.1 Retain moving-base, patch/result digest, secret-scan, and independent acceptance checks before draft delivery.
- [ ] 4.2 Mark success verified-for-review without submitting a GitHub approval or merge.
- [ ] 4.3 Clean successfully collected question and delivery attempts; preserve bounded forensic failures.

## 5. Acceptance

- [ ] 5.1 Add offline unclear → question → authorized answer → implementation → tests → draft acceptance.
- [ ] 5.2 Add dishonest cases for unauthorized comments, self-loops, duplicate/stale answers, failed tests, malformed questions, and moving bases.
- [ ] 5.3 Run affected packages, standalone acceptance, supply-chain, and strict OpenSpec.
- [ ] 5.4 Deploy reviewed digest-pinned workers with a separately scoped model credential and run a disposable real-GitHub conversational acceptance.
