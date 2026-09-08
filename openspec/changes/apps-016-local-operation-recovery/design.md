## Decisions

Generate Crockford ULIDs from a 48-bit timestamp and 80 random bits without adding dependencies. Atomically record creation identity, original node request, and the ensure replay key before dispatch. Preserve uncertain creations and retry the original node request, even after restart. Persist lifecycle operations before dispatch and use their unique operation IDs as node replay keys.

## Scope

No change to the core partition policy or public API. Existing runtime and distributed-I/O failure limitations remain explicit.
