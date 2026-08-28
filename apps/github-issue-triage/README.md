# GitHub issue triage worker

Deterministic reference triage worker for Factory's `issue-sdlc` operation. It reads the coordinator-written canonical document from `BARISTA_TRIAGE_OBJECTIVE_PATH` and writes one canonical closed decision to `BARISTA_TRIAGE_RESULT_PATH`.

The worker never receives forge, Host API, or delivery credentials. Issue and answer text remain inert data and cannot select commands, repository scope, acceptance, base, branch, or delivery. The reference uses explicit markers for offline acceptance:

- `[barista:needs-input]` asks one static clarification question unless an answer is present.
- `[barista:refuse]` returns a closed refusal.
- Other bounded issues return `ready` with static acceptance guidance.

A model-backed replacement may use a separately scoped model credential, but must preserve the same bounded decision schema and authority boundary.
