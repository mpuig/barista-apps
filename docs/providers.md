# Writing a Host API provider

A provider implements the open [Host API](../contracts/host-api/v1alpha1/openapi.yaml)
so portable apps run against it unchanged. `providers/local` is the reference;
this guide is for a third party (or Barista Cloud) implementing the same
contract.

## Obligations

1. **Serve the core profile.** Discovery, app install/validate,
   ensure/get/list/delete, exec, events, artifacts, and operations, using the
   published request/response/error schemas. Mutations accept an
   `Idempotency-Key` and are safely replayable.

2. **Advertise only what you support.** `GET /v1alpha1/discovery` returns the
   capabilities available *in the selected account/provider context* — never the
   union of a whole fleet. Invoking an unadvertised profile returns a
   `capability`-class error (HTTP 501); you must never fake it with weaker
   semantics.

3. **Expose no internals.** Sessions, apps, capsules, and artifacts are logical
   handles. Node addresses, object-store credentials, database ids, and
   privileged node credentials are never portable fields.

4. **Classify errors.** Use the nine error classes (authentication,
   authorization, capability, compatibility, conflict, quota, unavailable,
   terminal, invalid_request) so SDKs can decide retry vs. re-auth vs. terminal.

## Capability profiles

| Profile | Meaning |
|---|---|
| core | mandatory: discovery, lifecycle, exec, events, artifacts |
| `session.pause_resume` | pause and resume a session |
| `session.snapshot.exact` | exact (memory) snapshot |
| `session.fork` | branch exact state into a new session |
| `capsule.export` / `capsule.import` | portable capsule transfer |
| `grants.delegated` | mint scoped delegated grants, **and let a session refresh the one it holds** (`POST /v1alpha1/grants/refresh`) — a grant that cannot be refreshed dies a quarter of an hour into an hour-long mission, so there is no separate capability for it |
| `story.publish` | publish a redacted Session Story |
| `branch.evaluation` | fork fan-out with result receipts |

## Prove it

Run the black-box conformance suite against your endpoint:

```bash
cd conformance
uv run barista-conformance --endpoint https://your-provider.example --report report.json
```

The suite refuses to certify a profile you advertise but cannot demonstrate — a
skip never satisfies an advertised profile. The mandatory standalone profile
runs with Barista Cloud blocked and forbids any proprietary import.
