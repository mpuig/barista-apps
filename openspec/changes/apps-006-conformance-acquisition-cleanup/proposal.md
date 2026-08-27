## Why

The conformance suite records probe sessions only after delegated-credential acquisition returns. If a request raises after the coordinator session is created, acquisition never returns, the cleanup list is lost, and the suite leaves a `conf-probe-*` session behind. A failed certification run must not leak the infrastructure it created.

## What changes

- Give delegated acquisition a cleanup ledger before its first side effect.
- Append each session to that shared ledger as soon as creation succeeds.
- Keep the ledger reachable when acquisition raises, so runner cleanup still deletes every partial probe.
- Add a dishonest transport that raises after coordinator creation and prove no probe survives.

## Not in this change

- Changing conformance outcomes or turning cleanup failures into certification failures.
- Deleting operator-supplied sessions.
- Changing provider session lifecycle semantics.
