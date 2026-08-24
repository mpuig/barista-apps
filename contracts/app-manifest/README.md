# contracts/app-manifest

The versioned **Barista App Manifest**: a portable, least-privilege declaration
for packaging and running an app on any conformant Host API provider.

- **Schema:** [`v1alpha1/schema.json`](v1alpha1/schema.json) (JSON Schema
  draft 2020-12)
- **Media type:** `application/vnd.barista.app-manifest.v1alpha1+json`
- **Spec:** `openspec/changes/apps-001-portable-agent-apps/specs/app-manifest/spec.md`

## Identity and digest rules

An app's identity is `name` + `version` + `workload.digest`. The workload MUST
be pinned to an immutable OCI digest (`sha256:…`/`sha512:…`); a mutable tag in
`workload.image` is a human label only and never establishes identity. A
manifest without a digest is rejected before any session is created.

## Capability vocabulary

Capabilities are discoverable Host API profiles an app can require or optionally
use. Required capabilities must be advertised by the provider before any side
effect; the discovered optional set is delivered to the app at launch.

| Capability | Meaning |
|---|---|
| `session.pause_resume` | Pause and resume a session |
| `session.snapshot.exact` | Exact (memory) snapshot |
| `session.fork` | Branch exact state into a new session |
| `capsule.export` | Export a portable capsule |
| `capsule.import` | Import a portable capsule |
| `grants.delegated` | Mint scoped delegated grants |
| `story.publish` | Publish a redacted Session Story |
| `branch.evaluation` | Fork fan-out with result receipts |

## Permission vocabulary (actions)

Least-privilege Host API actions the app may perform:

`session.create`, `session.get`, `session.list`, `session.delete`,
`session.exec`, `session.attach`, `session.events`, `session.pause`,
`session.resume`, `session.fork`, `artifact.read`, `artifact.write`,
`capsule.export`, `capsule.import`, `story.publish`.

A host may grant less than requested only when the app declared a valid
`degraded_modes` entry describing which capabilities it can run without.

## Action scope: which sessions an action applies to

An action alone does not say *what it acts on*. `permissions.actions` therefore
carries a scope per action:

```json
"actions": [
  "session.create",
  { "action": "session.exec",  "scope": "created_sessions" },
  { "action": "artifact.read", "scope": "created_sessions" },
  { "action": "artifact.read", "scope": "own_session" }
]
```

| Scope | Means |
|---|---|
| `own_session` | Only the session the grant was minted for. |
| `created_sessions` | Only the sessions that session itself created — never a sibling, a parent, or any other session in the account. |

- A **bare action id** is exactly `{"action": <id>, "scope": "own_session"}`. It
  is kept so that every manifest written before scopes existed stays valid and
  keeps its meaning, and it is the only form in which `session.create` may be
  written. Any *wider* scope must be spelled out where the action is declared.
- The **object form always states its scope**; `scope` is required there. There
  is no way to declare a wide scope implicitly.
- The same action may appear **once per scope** (see `artifact.read` above).
  Declaring it twice for the same scope is a violation the schema cannot see —
  `uniqueItems` does not know that `"session.exec"` and
  `{"action": "session.exec", "scope": "own_session"}` are the same grant.
- **`session.create` takes no scope.** It is collection-level: it authorizes
  creating a child session and is bounded by `permissions.child_sessions`. The
  schema rejects the object form for it rather than leaving a provider to guess
  what a scope on it would have meant.
- Scope is **relative to the holder**. Inside `child_sessions.actions`,
  `own_session` is the child itself and `created_sessions` is the grandchildren
  the child would create.

A provider mints one selector per (action, scope) pair and **SHALL NOT** widen a
declared scope.

## Child-session authority

`permissions.child_sessions` bounds *how many* children an app may create and
declares *what they receive*:

```json
"child_sessions": {
  "max_concurrent": 16,
  "max_total": 256,
  "allow_descendants": false,
  "actions": [
    { "action": "session.exec",   "scope": "own_session" },
    { "action": "artifact.write", "scope": "own_session" }
  ]
}
```

- `actions` — what a child session receives. **The provider is the only minter:**
  it mints this set as the child's grant at child-session create. The parent
  asks for a child session and never handles that credential itself. There is
  deliberately no `grant.*` action, so a grant can neither mint nor narrow
  another grant.
- `allow_descendants` — whether a child may itself create sessions. **Absent or
  `false` means it may not**, even though the app itself may. A counts-only
  block (`max_concurrent`/`max_total` and nothing else) means a child receives
  no delegated authority at all: unchanged, pre-scope behaviour.
- When `allow_descendants` is not `true`, `actions` must not contain
  `session.create`. This one the schema *does* enforce.

## The subset rule — and what the schema does NOT enforce

> A child session's actions MUST be a subset of the app's own.

**JSON Schema cannot express this and `schema.json` does not enforce it.** The
rule relates one part of the document to another; a structural validator has no
way to compare `child_sessions.actions` against `permissions.actions`. A
manifest that hands its children `session.delete` while holding nothing of the
sort validates cleanly. **An implementer who assumes the schema stops that will
ship the hole.**

It is a **provider obligation, checked at install, before any side effect** —
not at first use. A manifest that grants its children more than it holds is
refused up front, and the response **names the offending actions**. Checking it
only at use would let an app install, fan out, and discover the problem on the
first child that tried to act.

Three parts, all outside the schema:

1. **Name subset.** Every action name in `child_sessions.actions` must appear in
   `permissions.actions` in some scope. An app cannot hand out a verb it was
   never granted.
2. **No downward widening.** A child entry scoped `created_sessions` reaches
   grandchildren. It requires `allow_descendants: true` *and* the app declaring
   that same action over its own `created_sessions`: an app may only delegate
   downwards a verb it exercises downwards itself.
3. **No duplicate (action, scope) pair** in either list.

Subset is deliberately not equality. A coordinator that can delete and exec in
its workers may give a worker only `session.exec`, and nothing here should push
it toward handing over everything it holds.

[`v1alpha1/rules.py`](v1alpha1/rules.py) is the stdlib-only reference
implementation, runnable on any manifest:

```bash
python3 contracts/app-manifest/v1alpha1/rules.py apps/factory/manifest.json
```

Validate shape with `schema.json` **and then** semantics with these rules. One
without the other is not validation.

## Secrets

`permissions.secrets` entries carry a `name` and a provider-resolvable `ref`
only. Plaintext secret values are rejected by the schema (the entry object is
closed). Harness config, transcript formats, and model identifiers live under
namespaced `metadata` or artifacts and stay opaque to the host.

## Examples

- [`v1alpha1/examples/factory.json`](v1alpha1/examples/factory.json) — full
  manifest (capabilities, scoped actions, child-session authority, delegated
  `grant://` credential, degraded mode, state transfer).
- [`v1alpha1/examples/nested-fanout.json`](v1alpha1/examples/nested-fanout.json)
  — the only shape in which a descendant may create sessions:
  `allow_descendants: true`, with every downward-delegated action one the app
  exercises downwards itself.
- [`v1alpha1/examples/minimal.json`](v1alpha1/examples/minimal.json) — smallest
  valid manifest.
- `v1alpha1/invalid/` — fixtures that MUST fail **schema** validation (missing
  digest, plaintext secret, unknown capability). Used by the golden tests.
- `v1alpha1/semantically-invalid/` — fixtures that **pass** the schema and MUST
  be refused at install by the rules above. They exist to make the schema's
  limits impossible to overlook.
- `v1alpha1/compat/pre-scope-factory.json` — a frozen, byte-for-byte copy of
  `apps/factory/manifest.json` as it stood *before* action scopes and child
  authority existed (flat action list, counts-only `child_sessions`;
  `sha256:c646a0d5830bb1886a9ac1e5c5f40ee0a994f0ef22b5c11d544ed8bb4d122940`).
  The golden tests assert it still validates and still means *no child
  authority*. Do not edit it — it is evidence, not an example.
