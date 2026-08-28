# Change Agent

A minimal single-agent repository job that exercises the shared App Run model
without Factory coordination.

The `change` operation:

1. resolves `bindings.workspace` once to an exact Git commit;
2. materializes a bounded detached checkout;
3. optionally resolves a bounded local text/specification objective;
4. runs the explicit `command` and then `check` argv from typed input;
5. creates and registers a binary-capable patch;
6. optionally creates a local branch only after the check succeeds;
7. registers a canonical terminal App Run result on its provider-injected owning
   session.

Objective content is written to `BARISTA_OBJECTIVE_PATH`; it never becomes a
command, check, limit, credential, or delivery. A failed check still returns the
recoverable patch and receipts but never creates the requested branch.

Example input:

```json
{
  "command": ["python", "tools/apply_change.py"],
  "check": ["python", "-m", "pytest", "-q"],
  "timeout_seconds": 900,
  "branch": "barista/verified-change",
  "commit_message": "Apply verified change"
}
```

Launch with a local project binding:

```sh
barista-app run \
  --app ./apps/change-agent \
  --operation change \
  --input change.json \
  --repo file:///absolute/path/to/project \
  --repo-ref main \
  --output result.json
```

The app hard-caps workspaces at 256 MiB and patches at 16 MiB. A smaller input
limit or discovered provider `max_binding_bytes` limit can tighten the bound;
neither can widen it.
