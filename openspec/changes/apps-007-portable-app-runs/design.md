# Design — portable app runs

## D1. A run is an application contract, not another provider scheduler

The Host API already owns installation, idempotent session creation, lifecycle,
exec, events, grants, and artifacts. Adding `/runs` with another state machine
would duplicate those resources and force every provider to reconcile two
records of the same workload.

A portable App Run is therefore a canonical client/app protocol over the
existing Host API:

1. resolve and validate the app manifest and run declaration;
2. validate the envelope before any provider mutation;
3. install the immutable manifest when necessary;
4. ensure one owning session under a stable idempotency key;
5. deliver the canonical envelope at session creation;
6. observe the owning session and its events;
7. collect and verify the canonical result before cleanup.

A coordinator such as Factory may create child sessions, but that is app
behavior under its manifest authority. The provider does not interpret its task
graph.

## D2. App source, project source, and executable identity are three facts

An app repository answers where a manifest was discovered. A project repository
is a resource bound to a run. Neither is executable identity: the manifest's OCI
digest remains that.

`--app factory@0.4.2` may resolve an installed/catalog app. The current Host API
can install an app but cannot read its manifest back; a runner would therefore
have to guess its operations or carry a provider-specific catalog. This change
adds a read on the existing installed-app resource so the runner can retrieve
the validated manifest without reinstalling it. That is app discovery, not a new
run scheduler, and the response contains references rather than resolved
secrets.

A Git or file locator may resolve a manifest for development or third-party
distribution, but the resolver MUST select an exact source revision, validate
the manifest, and record both source revision and workload digest before launch.
It MUST NOT clone a repository and execute an unpinned script merely because the
repository was passed as `--app`.

Network resolution is adapter behavior. The mandatory standalone tests use a
local repository and require no registry or forge connection.

## D3. Inputs, bindings, secrets, and deliveries remain separate

One opaque `config` object would be easy to pass and impossible to secure or
explain. The envelope separates four roles:

- **input** is small, immutable app-specific JSON with a declared media type;
- **bindings** are named references to resources the app may read or act on;
- **secrets** map local names to `secret://` references and never carry values;
- **deliveries** request named outputs or external side effects.

Binding and delivery kinds use namespaced identifiers. The core contract can
recognize `sh.barista.git.repository` as a string without teaching a provider
how Git works; a source-control adapter interprets it. App manifests declare the
kinds an operation accepts, and unknown or undeclared names fail before launch.

Convenience CLI flags are projections of this envelope:

```text
--repo URL                 -> bindings.workspace
--issue URL                -> bindings.objective
--publish draft-pr         -> deliveries.change
--mission FILE             -> input value/media type
```

The canonical envelope, not the convenience spelling, is the portable API.

## D4. An issue is objective content, never policy

A forge issue can say what change is wanted. It cannot be the complete mission:
issue authors are not entitled to select credentials, widen egress, add workers,
raise budgets, replace acceptance criteria, or choose a publication target.

Factory's `software-change` operation combines an issue binding with a bounded
workflow declared by the app or supplied as validated mission input. The issue
body is quoted as untrusted objective content. Any instruction in it that
conflicts with run policy is data, not authority.

This distinction is also what permits non-issue objectives: a local brief and a
forge issue may feed the same workflow without changing its bounds.

## D5. Publication is explicit and failure does not publish by default

Creating a branch or pull request changes an external system. No app run gains
that side effect merely by receiving a repository binding. The envelope must
request a declared delivery kind and name the credential reference it uses.

The first Factory delivery is a **draft** pull request. It records the objective,
exact base and head revisions, app/manifest/workload identities, checks, and
receipt references. A failed independent check yields a failed run with a local
patch/branch artifact and preserved forensics; it does not publish unless a
separate explicit policy requested a failed draft for inspection.

GitHub is one forge adapter, not Host API vocabulary. A GitLab merge request or a
plain patch can satisfy other declared delivery kinds without changing the Host
API.

## D6. Lifecycle belongs to the run declaration

A universal `run` command must not pretend every app terminates. A manifest run
operation declares one lifecycle:

- `job`: reaches a terminal success or failure and returns outputs;
- `service`: reaches ready and remains available through endpoints;
- `interactive`: reaches ready and returns an attachable session;
- `coordinator`: is a job that may supervise child sessions and must collect
  their evidence before reaching a terminal result.

The distinction controls what the runner waits for and what constitutes a
result. It does not add provider states: session lifecycle and app result remain
the observable facts underneath.

## D7. Run declarations are additive and app-specific schemas stay opaque

`runs` is an optional map in the App Manifest. Each named operation declares its
lifecycle, input media type, accepted binding kinds by binding name, and accepted
delivery kinds. It may embed a Draft 2020-12 input schema; when it does, the
runner validates it before installation or session creation. When only a media
type is declared, the app remains responsible for semantic validation and must
fail before acting on bindings.

Existing manifests have no `runs` member and remain valid. They can still be
installed and launched through `ensure_session`; a typed runner reports that no
run operation was declared rather than guessing from `args` and `env`.

Harness configuration stays in adapter metadata or app input. No Pi-, Claude-,
Codex-, GitHub-, or Factory-specific property enters the Host API.

## D8. Delivery at launch is canonical JSON; credentials are references

The runner serializes the envelope with the ecosystem's canonical JSON rules and
delivers it as non-secret launch configuration. The initial transport is
`BARISTA_APP_RUN`; large content is represented by a binding rather than copied
into the envelope. The provider also injects the reserved
`BARISTA_APP_SESSION_ID` with the opaque Host API handle it allocated before the
workload started. The caller cannot supply or override that value. An app can
therefore register results on its own durable scope without guessing by name,
creating a duplicate session, or learning a node identifier.

Apps may keep an established app-specific bootstrap variable. Factory does:
`$BARISTA_FACTORY_MISSION` remains the canonical in-session mission mechanism.
Its run adapter extracts the validated mission from the envelope and supplies
that variable before the coordinator starts. There is no silent fallback from
an explicitly supplied unreadable mission path.

Raw forge or repository credentials never appear in `BARISTA_APP_RUN`. The
envelope contains a secret reference name, and the manifest/provider resolves
that reference into the separately declared environment variable under normal
least-privilege rules.

## D9. The owning session holds the result until collection

A job/coordinator writes canonical result JSON at a declared path in its own
session and registers its digest and media type as an artifact before it may be
reaped. A coordinator obtains that owning handle from the provider-injected
launch context described in D8. The runner reads the bytes while the owning session still exists and
verifies the digest. Successful child workers may disappear; the owning session
and result are the durable rendezvous.

A result names the run, operation, state, exact app/workload identity, resolved
bindings (including an exact Git commit), checks/receipts, and outputs. An output
may be an artifact, endpoint, session, or external reference such as a draft pull
request URL. The result never contains a credential value.

The current artifact registry records references rather than bytes, so this
change does not claim that deleting the owning session preserves arbitrary
result bytes. Cleanup happens only after collection, and the runner makes local
output persistence explicit.

## D10. Repository distribution and verification are app concerns built on one binding

A Git repository binding resolves a floating branch or tag once and records the
exact commit. A single-agent app can acquire that commit directly. Factory
acquires one base and gives each worker an equivalent workspace; an optional
snapshot/fork capability may optimize this, but the core fallback cannot depend
on it.

Parallel changes do not share a writable checkout. Workers return declared
patches/artifacts, and a coordinator-owned integration stage applies them to a
clean checkout. Independent checks run against that clean integrated tree, not
against the worker's self-marked workspace. Only the verified integrated head is
eligible for pull-request delivery.

Submodules, LFS, repository size/file limits, and credential use are reported in
binding provenance. Unsupported features fail explicitly rather than silently
producing a partial checkout.

## D11. The first proof uses two app shapes and an offline forge

A schema that only Factory exercises will become Factory's schema. Acceptance
therefore proves:

1. a single-agent repository job consuming the same Git binding and returning a
   patch/result; and
2. Factory consuming a Git binding plus objective, coordinating workers, running
   an independent integration check, and requesting a draft-PR delivery.

The forge test double is deliberately offline and dishonest cases are included:
it tries to accept a raw token, move a resolved base ref, publish after a failed
check, and publish to a repository outside the declared binding. Each must be
refused. The same app/run envelopes are then eligible for managed-provider
acceptance by changing endpoint and credential only.
