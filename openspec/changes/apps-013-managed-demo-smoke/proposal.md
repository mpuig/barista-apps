# Proposal: Repeatable managed demo smoke gate

## Why

The managed Factory, session lifecycle, public applications, and real agent
checks were proven manually, but a release could still translate a skipped test
into green or omit one of those checks without leaving a machine-readable
record. Operators need one bounded command whose default profile is cheap and
whose model profile never transports model credentials.

## What changes

- Add `barista-managed-smoke`, an opt-in managed acceptance command that emits a
  bounded JSON report.
- Make the default profile run real lifecycle continuity and a dependency-gated
  Factory mission through public Host API requests.
- Add named public URL checks and an optional model profile for preinstalled
  Claude, Pi, and Codex apps.
- Treat every managed skip as a failed release gate.
- Keep model credentials in provider-resolved app secret references; the runner
  accepts app names, argv, and non-secret expected markers only.

## Portability

The command uses only the published Host API and HTTP public endpoints. It
imports no Cloud implementation and the existing Cloud-blocked standalone
acceptance remains mandatory and unchanged.
