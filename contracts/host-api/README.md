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
