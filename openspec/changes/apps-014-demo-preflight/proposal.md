# apps-014: No-spend managed demo preflight

## Why

The managed release gate proves the environment, but its default profile does
not pull or start the three showcase agent images and its model profile spends
provider tokens. A presenter needs one rehearsal command that warms immutable
images, proves provider bindings without printing them, exercises pause/resume,
and leaves no sessions behind.

## What changes

- Add an explicit `preflight` profile to `barista-managed-smoke`.
- Run the existing lifecycle, dependency-gated Factory, and named public URL
  checks first.
- Reuse bounded configured agent argv for no-model version/binding probes.
- Exercise pause/resume and unconditional deletion for every warm-up session.
- Document reviewed Claude, Pi, and Codex preflight commands.

## Boundaries

- The profile accepts app names, bounded argv, and expected non-secret markers
  only.
- Credential values remain provider-resolved and are tested only for presence;
  they never enter configuration, argv, output, or reports.
- Preflight evidence is distinct from paid model evidence and does not claim a
  provider inference succeeded.
