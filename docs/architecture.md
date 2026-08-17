# Architecture: kernel, provider, app

Barista is three layers with one-way dependencies. Each layer knows only the
contract below it.

```
┌─────────────────────────────────────────────────────────────┐
│ apps/  — portable apps + adapters (Pi, Claude, Codex,         │
│          Factory, Lift, Story) built on the SDK               │
│  sdks/python — provider-neutral client + adapter interface    │
├─────────────────────────────────────────────────────────────┤
│ Host API (contracts/host-api) — the open wire contract        │
│  providers/local (this repo) · Barista Cloud · third parties  │
├─────────────────────────────────────────────────────────────┤
│ barista.sh — the execution kernel (Node Agent / Contract A)   │
└─────────────────────────────────────────────────────────────┘
```

## The three seams

1. **Kernel ↔ provider.** A Host API provider maps the open contract onto a
   Barista Node Agent (Contract A). Apps never see Contract A, node addresses,
   or node credentials. `providers/local` is the reference mapping; Barista
   Cloud is another provider; a third party can write their own.

2. **Provider ↔ app.** Apps target the **Host API** and discover capabilities.
   The same app runs against a local provider or Barista Cloud by changing only
   the endpoint/credential — never a branch on provider name. Optional powers
   (pause/resume, exact snapshot, fork, capsules, delegated grants, story
   publication, branch evaluation) are individually discoverable profiles.

3. **App ↔ harness.** Harness-specific behavior (Pi, Claude Code, Codex) lives
   in **adapters**, never in the Host API or the manifest envelope. Adapters
   preserve harness-native state as opaque attachments and report fidelity
   honestly.

## What lives where

| Concern | Home |
|---|---|
| Hypervisor lifecycle, snapshots, fork, capsules | `barista.sh` (kernel) |
| Open wire contract (Host API, App Manifest, Story) | `contracts/` (this repo) |
| Reference single-user provider | `providers/local` (this repo) |
| Provider-neutral SDK + adapter interface | `sdks/python` (this repo) |
| Portable apps + harness adapters | `apps/` (this repo) |
| Multi-tenant service, billing, global registry, sharing | Barista Cloud (proprietary) |

The open layers never import or depend on Barista Cloud. The mandatory
standalone conformance profile enforces this: it runs with Cloud DNS blocked and
fails on any proprietary import or Cloud network attempt.
