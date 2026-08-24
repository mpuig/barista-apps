# contracts/host-api

The open, provider-neutral **Barista Host API**: the wire contract through
which portable apps control sessions and artifacts without addressing a
privileged Barista node directly.

- **Spec:** `v1alpha1/openapi.yaml` (OpenAPI 3.1)
- **Streaming:** `v1alpha1/streaming/event.schema.json` (SSE payloads),
  `v1alpha1/streaming/attach-frame.schema.json` (WebSocket attach frames)
- **Media type:** `application/vnd.barista.host-api.v1alpha1+json`
- **OpenSpec:** `openspec/changes/apps-001-portable-agent-apps/specs/host-api/spec.md`

## Profiles

The **core profile** is mandatory for every provider: discovery, app install,
ensure/get/list/delete, pause/resume, exec, attach, events, artifacts, and
operations. Optional state powers are individually discoverable capabilities:

`session.pause_resume`, `session.snapshot.exact`, `session.fork`,
`capsule.export`, `capsule.import`, `grants.delegated`, `story.publish`,
`branch.evaluation`.

A provider returns a `capability`-class error (HTTP 501) when an unadvertised
profile is invoked and never fakes it with weaker semantics.

## Refreshing a delegated grant (`grants.delegated`)

`POST /v1alpha1/grants/refresh` replaces the delegated grant that authenticates
the request. It exists because a grant is delivered into a session once, as an
environment variable that cannot be rewritten in a running process, while the
credential expires long before a long mission does.

Three properties are worth knowing before you depend on it:

- **The credential is the subject, and there is no request body.** You refresh
  what you hold, and the replacement's resource and actions are copied from the
  stored grant. Nothing on the wire could widen it — that is the entire
  difference between this and a `grant.issue` operation, which the contract
  deliberately does not have. A tenant credential has nothing to refresh and is
  refused.
- **Refresh rotates; it does not extend.** The moment the replacement is issued,
  **the previous secret stops working**. A caller that issues a refresh and
  loses the response has **locked itself out** — the replacement is returned
  once and is not recoverable, and there is no `Idempotency-Key` on this path
  because replaying a refresh would mean the provider keeping a second live copy
  of a credential. This failure is deliberate: it is loud and immediate, and it
  bounds what a leaked secret is worth to the refresh interval rather than to
  the session's lifetime. Refresh with enough margin that a lost response leaves
  time to be re-provisioned, and never retry a refresh blind.
- **A lapsed grant cannot be refreshed.** Expired, revoked, and already-rotated
  secrets are all refused, so expiry and revocation stay final. Nothing caps how
  many times a live grant may be refreshed: the bound is the session, which means
  **deleting a session revokes the grants bound to it** — an obligation on
  `deleteSession`, covering every path that deletes a session, because it is the
  only thing that ends a chain. A provider that skips it has a session-bound
  grant renewing itself forever after its session is gone, and no
  maximum-lifetime ceiling catches that: no single credential ever exceeds it.
  For the same reason **a grant with no session binding cannot be refreshed
  either**. Without a session there is no bound, and a chain would let a holder
  outlive a maximum-lifetime ceiling in small steps while no single credential
  ever exceeded it. That ceiling exists to force a re-issue, which is a
  re-decision; a chain that never trips it removes the review.
- **A refusal leaves the presented credential working.** Rotation is one
  transaction whose failure direction is rollback, not revoke-then-fail: a caller
  stranded by a refused refresh would hold nothing and have no way to obtain
  anything. With the atomicity rule (no instant where both secrets work, none
  where neither does), every outcome leaves the caller holding exactly one
  working credential.

There is **no separate capability id and no vendor extension** for refresh.
`grants.delegated` is the whole gate: a delegated grant that cannot be refreshed
dies a quarter of an hour into an hour-long mission, so refresh is part of what
advertising delegated grants means. An app checks `grants.delegated` and depends
on it; the conformance suite fails a provider that advertises the profile without
offering the operation.

## Design rules baked into the contract

- **No provider internals.** Sessions, apps, capsules, and artifacts are
  logical handles. Node addresses, object-store credentials, database ids, and
  privileged Node Agent credentials are not portable fields.
- **Idempotent mutations.** Every mutation accepts an `Idempotency-Key`;
  replay returns the original logical resource and operation.
- **Resumable streaming.** SSE events carry stable cursors (`Last-Event-ID`);
  operations remain readable after client disconnect.
- **Byte-clean and terminal attach.** `attach?mode=raw` is byte-clean non-PTY;
  `attach?mode=pty` is terminal I/O with resize.
- **Classified errors.** `Error.class` ∈ {authentication, authorization,
  capability, compatibility, conflict, quota, unavailable, terminal,
  invalid_request} so a client can decide retry vs. re-auth vs. terminal.
- **Namespaced extensions only.** Provider extensions live under `extensions`
  and never alter core fields.
