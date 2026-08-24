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
