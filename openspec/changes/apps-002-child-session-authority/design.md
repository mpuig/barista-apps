# Design — child-session authority

## D1. Why the counts were never enough

`child_sessions: {max_concurrent, max_total}` reads like a complete thought and
is not. It answers "how many", which is a budget question, and leaves "what may
the parent do to them" and "what do they get" unanswered — which are authority
questions. A provider reading the manifest has a quota and no basis for a
selector.

The tell is that the ratified `factory-app` requirement already depends on the
missing half. Its scenario turns on a worker *lacking child-create permission*,
which is a permission the contract has no way to grant or withhold.

## D2. The provider stays the only minter

The obvious shape — let the coordinator mint its workers' grants — is wrong, and
the reason is already settled elsewhere: there are deliberately no `grant.*`
actions, so a grant can neither mint nor revoke, including itself. A grant that
could mint a narrower grant would be a key that makes keys, and "narrower" is
not a property anything checks at the moment of use.

So delegation stays vertical through the provider: the app asks for a child
session, and the provider — which already holds the manifest, already mints at
session create, and is the only party that can see both levels at once — mints
that child's grant. The coordinator never handles a credential it did not
receive.

This also makes the ratified scenario enforceable in the right place. "A worker
without child-create permission calls session create → denied" is a decision the
provider makes from the manifest, not a rule the coordinator is trusted to obey.

## D3. Subset, checked at install rather than at use

A child's action set MUST be a subset of the app's own. Checking that at install
is what makes it meaningful: a manifest that grants its children more than it has
is refused before anything runs, with the offending actions named. Checking it
only at use would mean an app could install, fan out, and discover the problem on
the first child that tried to act — which is the shape of failure this whole
contract exists to avoid.

Subset is deliberately not "equal". A coordinator that can delete sessions and
exec in them may reasonably give a worker only `session.exec`, and nothing in the
contract should push it toward handing over everything it holds.

## D4. Lifetime is a separate problem, and it is the harder one

Scoping a grant correctly does not make it live long enough. A delegated grant
lives 15 minutes; a factory mission's default task timeout is 3600 seconds; and
the grant arrives as an environment variable, which cannot be rewritten in a
running process.

The options are all decisions rather than plumbing, which is why this change does
not pick one:

- a longer TTL for app grants — cheapest, and weakens the property that a leaked
  delegated credential is worth minutes;
- a channel that can deliver a fresh secret into a running session — the honest
  fix, and new surface;
- a narrow exception to the no-`grant.*` rule permitting self-renewal without
  widening — smallest contract change, largest security argument to win.

Naming it here so that "the factory is an app now" cannot be claimed on scoping
alone. An app that authenticates for fifteen minutes of an hour-long mission is
not finished.

## D5. Why not add `worker.*` to the vocabulary

Because the ratified factory app does not need it, and adding it would put a
provider-specific primitive into a portable contract.

`factory-app` coordinates *worker sessions* through the Host API — "worker" is
the role a session plays, not a distinct resource. A provider may additionally
offer a named, invocable worker object (barista-cloud does, and it is a better
fan-out primitive than raw session creation), but an app that depended on it
would no longer be portable, which is the one property this contract exists to
protect.

The collision is purely nominal and cost real analysis time: two different things
called "worker" in two repos. Recorded so the next reader does not spend the same
afternoon.

## D6. Per-action scope, not a second block (task 1.2)

Two shapes could carry "these actions, on the sessions I create":

- **A second block** — `permissions.actions` for my own session and, say,
  `permissions.actions_on_created_sessions` beside it.
- **A per-action scope** — one list whose entries name the action *and* what it
  applies to.

**The per-action scope is implemented.** Four reasons, in the order they decided
it:

1. **Scope is a property of the grant, not of the app.** A provider mints one
   selector per (action, resource) pair. A list of `(action, scope)` pairs *is*
   that set; two blocks are a set that has been split and must be rejoined by
   every reader, correctly, every time.
2. **`child_sessions.actions` reuses the same item type.** With a second block,
   every level needs two: `child_sessions.actions` *and*
   `child_sessions.actions_on_created_sessions`. Blocks multiply per level;
   scopes do not. This alone settled it — the child level is the whole point of
   the change, and the shape that makes it awkward is the wrong shape.
3. **The subset check compares pairs.** `child ⊆ app` over pairs catches a
   scope widening as naturally as a missing action. Split across blocks, the
   obvious implementation compares names in one list and silently misses that
   the child was handed a wider reach — the same class of bug as the flat list
   we are replacing.
4. **A third scope is an enum value, not a fourth block.** If "the sessions in
   my mission" ever becomes a real scope, one enum grows. With blocks, the
   permissions object grows a member and every consumer changes.

Making the scope explicit *at the point of declaration* was the constraint, and
it is met by construction: the object form **requires** `scope`. The bare action
id survives as exactly `{"action": …, "scope": "own_session"}` — the reading
every pre-change manifest already had — so backward compatibility costs nothing
and there is still no way to obtain a wider scope by omission. `session.create`
is the one action the schema refuses in the object form: it is collection-level,
bounded by `child_sessions`, and a scope on it would mean nothing.

## D7. What conformance can prove, and where it stops

`factory-app`'s scenario becomes five cases under the `grants.delegated` profile.
Two need only the ordinary credential — a child-authority manifest installs, and
an over-delegating one is refused at install naming the offending action. The
scenario itself needs more: the same request has to succeed for the coordinator
and be refused for its worker, which means holding both credentials.

**Host API `v1alpha1` has no endpoint that hands a delegated grant to a client.**
The provider mints a child's grant and delivers it *into* the child session, as a
`grant://` reference resolved into its environment. A black-box suite runs
outside every session; it cannot obtain one through the published contract, and
reaching around the contract would forfeit the property that makes conformance
worth anything.

So those three cases take operator-supplied credentials and otherwise **skip
with that reason**. Since a skip never satisfies an advertised profile, a
provider advertising `grants.delegated` without supplying them is reported *not
conformant* — the suite declines to certify delegation it could not watch happen.
The alternative, a case that passes because it never ran, is the failure mode
this whole contract exists to avoid.

Each case asserts both sides, so a provider that denies everything fails rather
than passes, and a dead credential is detected rather than mistaken for a
refusal. The remaining honest gap: whether a future change adds a way to obtain
a delegated grant through the API, or operator-supplied probes stay the answer.
That is a contract decision, not an implementation detail, and it is not made
here.
