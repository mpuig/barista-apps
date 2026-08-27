## Why

**Factory has never been run the way it is meant to be used.** Everything below
was measured against Barista Cloud beta on 2026-08-27, with a mission that
completed: `produce` and `consume` both `ok`, checks passing, receipts
harvested, workers reaped.

It completed with the coordinator running *on a laptop*.

That is the one arrangement the product exists to avoid. `factory-app`'s
ratified purpose is "durable worker sessions", and tutorial 10's promise is that
"your laptop can close and the factory keeps working". A coordinator in a
terminal is neither, and it means the load-bearing claims have no test behind
them:

- **"coordinator resumes after pause"** is a ratified scenario under *One
  durable coordinator SHALL own mission state*. Nothing exercises it. The
  coordinator has never been paused mid-mission.
- **"same mission runs locally and in Cloud"** is a ratified scenario under
  *Factory SHALL be an ordinary portable app*. `acceptance/` runs a Factory
  mission, but only against the local provider with Cloud blocked. The Cloud
  half of that scenario is unproven.

Three concrete things block an in-session run, and each was hit rather than
predicted:

1. **The published coordinator image does not run the coordinator.**
   `barista-factory:0.1.0` and `:0.2.0` both carry
   `Cmd = sh -c "echo 'coordinator ready'; exec sleep infinity"`. The real
   binary *is* present at `/usr/local/bin/barista-factory`; the image simply
   never invokes it. So the app as published boots into a sleep.
2. **Mission delivery is unspecified and the two halves disagree.** The
   manifest's entrypoint reads `/work/mission.json`; nothing writes it.
   `__main__.py` also accepts `$BARISTA_FACTORY_MISSION`. Neither is in the
   spec, and the e2e only worked by using the second.
3. **A coordinator that raises at startup takes the instance with it.** With
   `BARISTA_HOST_API_ENDPOINT` absent, `Config.from_env()` raises, the workload
   exits, and the session surfaces as `GUEST_UNREACHABLE` — which reads as
   infrastructure failure rather than a misconfigured app.

And one claim that is weaker than it looks. The run reported
`credential: {active: true, refreshes: 1}`, but `CredentialKeeper.establish()`
refreshes **once on purpose** — a grant arrives with no expiry attached, so
asking for a replacement is the only way to learn its lifetime through the
contract — and `_adopt` is what increments that counter. The mission ran 14
seconds; `ensure_fresh()` fires at a 180s margin of a 900s lifetime. So refresh
is proven to *work*, and a mission outliving one grant lifetime is not proven at
all.

## What changes

**A managed-provider end-to-end acceptance run, with the coordinator in a
session**, and the two contract gaps it depends on closed.

- **Mission delivery becomes a requirement** rather than two undocumented
  mechanisms. `$BARISTA_FACTORY_MISSION` wins: it needs no writable path
  agreed in advance, no ordering between session creation and file placement,
  and it is the mechanism that actually works today. `/work/mission.json` stays
  supported for an operator running the binary by hand.
- **A coordinator image that runs the coordinator**, whose entrypoint matches
  what the manifest declares.
- **A startup failure reports as a failed app**, not as an unreachable guest:
  missing configuration is detected and reported before the process exits.
- **The acceptance flow gains a managed-provider mission** covering what the
  local one cannot: pause/resume mid-mission, a mission longer than one grant
  lifetime asserting `refreshes > 1`, and the failure paths.

## Not in this change

- Rewriting the mission schema or the scheduler. apps-004 shipped both; this
  change runs them somewhere they have not run.
- A real agent worker image. `prompt` tasks need an adapter baked in and a model
  credential, which is its own change; missions here use `command` tasks.
- Cloud-side work. Everything here is app and contract; the provider is
  conformant for what this needs (`core`, `session.pause_resume`,
  `grants.delegated`, certified 2026-08-27).
- Tutorial 10's Claude-Code-skill flow, which drives a different factory over
  `/v1`. Reconciling the two stories is worth doing and is not this.

## Impact

- **The published images change.** Anyone depending on `barista-factory:*`
  booting into a sleep — nothing should, but it is what they do today — sees a
  container that runs a coordinator and exits when the mission ends.
- **A mission that outlives its grant is exercised for the first time.** If
  renewal is broken under real elapsed time, this is what finds it, and finding
  it is the point.
- **Acceptance gains a run that needs a provider and credentials**, so it cannot
  be part of the offline standalone flow. It is a separate, opt-in target.
