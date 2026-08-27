# Design

## D1 — Publish the cleanup ledger before acquisition starts

`_acquire_delegated` currently owns a local `created` list and returns it inside `AcquiredDelegation`. That works for represented failures, but not exceptions: assignment to `config.acquired` never happens, so runner cleanup cannot discover what was created.

`delegated_credentials` will install a pending `AcquiredDelegation` on the config before calling acquisition. Its `sessions` list is passed into `_acquire_delegated`, which appends each successful creation immediately. A normal return replaces the pending record with the final reason and probe while retaining the same list. If acquisition raises, the pending record remains reachable and the runner's existing release step deletes the partial resources.

This keeps cleanup ownership in one place. `_acquire_delegated` does not delete in scattered error branches, and case execution still reports the original exception rather than replacing it with cleanup behavior.

## D2 — Operator-owned sessions remain outside the ledger

The pending record is created only on the self-acquisition path. `config.delegated_probe` continues to mean operator-supplied credentials and sessions; the suite may refresh those credentials but never adds their session ids to its cleanup list.

## D3 — Cleanup remains best effort

A provider failure that also prevents deletion should not hide the conformance result. `release_delegated` keeps its best-effort behavior. The new test uses a provider that fails only the acquisition request, then accepts cleanup, so it proves the suite attempts and completes cleanup when the contract remains available.
