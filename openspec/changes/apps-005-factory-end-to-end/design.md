# Design

## D1 — The mission arrives in the environment

Two mechanisms exist and neither is specified: the manifest's entrypoint reads
`/work/mission.json`, and `__main__.py` falls back to
`$BARISTA_FACTORY_MISSION`. The file is the one that has never worked, because
nothing writes it — there is no step between "create the session" and "the
workload starts" where a caller could put it there through the Host API.

`$BARISTA_FACTORY_MISSION` wins:

- it needs no writable path agreed in advance, and no `/work` to exist — which
  it does not in every image, and `working_dir` does not create it;
- session `env` is delivered atomically with creation, so there is no ordering
  problem and no window where the coordinator starts without its mission;
- it is what the measured run used.

The file path stays supported for an operator running the binary by hand, where
a path is the natural thing. `_load_mission`'s existing precedence is already
right and does not change: an explicitly named path that is missing is an error,
never silently replaced by whatever the environment happens to hold.

**Why this is a requirement and not just a default.** An app that cannot be told
what to do is not portable, and "how does the mission get in" is exactly the
kind of thing each provider would otherwise answer differently.

## D2 — A misconfigured app must fail as an app

With `BARISTA_HOST_API_ENDPOINT` absent the coordinator raised, the workload
exited, and the session reported `GUEST_UNREACHABLE`. Every part of that is
working as designed — a workload that exits ends its instance — but the report
names the wrong layer, and an operator reading it goes looking at the node.

The coordinator therefore validates what it needs **before** doing anything that
could exit, and reports a missing endpoint or mission as its own failure, with
the variable named. It still exits non-zero; what changes is that the last thing
written is an explanation.

This does not try to make a crashed workload survive. It makes the difference
between "this app was misconfigured" and "this guest is unreachable" visible in
the place an operator looks first.

## D3 — Pause/resume is tested by pausing, not by restarting the process

The ratified scenario is *"the coordinator pauses or restarts mid-mission →
it reconstructs task state and continues without creating a second worker for an
already accepted attempt"*. Two readings, and the weaker one is easy to satisfy
by accident: kill the process and run it again against the same state file.

The acceptance run does the stronger one — `POST /sessions/{id}/pause` on the
coordinator's own session while tasks are in flight, then `resume` — because
that is the claim the product makes and the one nothing currently exercises. The
assertion is on worker identity: the same accepted attempt must not produce a
second worker.

This needs a mission slow enough to still be running when the pause lands, so
tasks sleep. That is a real cost and it is deliberate: a mission that finishes
before it can be interrupted proves nothing about interruption.

## D4 — Renewal is proven by elapsed time, not by a shortened lifetime

`refreshes > 1` is the only evidence that a mission outlives one grant lifetime.
It could be reached cheaply by configuring a tenant with a very short grant TTL,
and that would be a worse test: it would exercise the margin arithmetic while
leaving the thing anyone actually doubts — that a long-running coordinator keeps
working for an hour — untouched.

So the renewal case runs against the real 900s lifetime and takes longer than
`900 - margin` (~720s) of wall clock. It is marked slow and excluded from the
default run.

**What it asserts** is renewal, not survival of everything else: `refreshes > 1`,
`active` still true, `authority_lost` still null, and the mission completing.
`inactive_reason` must stay null throughout — an `inactive_reason` here means
the keeper gave up and the mission was bounded after all.

## D5 — Acceptance splits by what it needs

`acceptance/` today is one offline flow with Cloud blocked, and that property is
worth keeping exactly as it is: it proves the open stack owes Cloud nothing.

The managed run needs the opposite — a real provider, a credential, and network.
So it is a **separate target**, opt-in, skipped with a stated reason when
`BARISTA_HOST_API_ENDPOINT` is absent rather than failing. Neither run's
guarantee is weakened by the other's existence, and the standalone guard stays
process-wide where it is.

## D6 — The image runs what the manifest says it runs

The published coordinator images carry a `Cmd` that sleeps. The fix is not to
special-case the entrypoint at install time; it is for the image to invoke the
binary it already contains, so that `docker run` and a Host API session do the
same thing.

The manifest's `entrypoint` stays explicit rather than relying on the image
default. An app manifest that names its entrypoint is readable without pulling
the image, and the two agreeing is what this change restores.
