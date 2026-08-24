# barista-apps

The **vendor-neutral userland** for Barista: an app written here runs unchanged
against a local single-user host or a managed service, without depending on
either implementation's private API. That portability is the product — every
convention below exists to protect it.

`CONTRIBUTING.md` has the change workflow and the non-negotiable boundaries;
read it first. This file is the operating detail that sits under it.

## Layout

Each package is independently versioned and **tested from its own directory**,
which is how CI runs them:

| path | what it is |
|---|---|
| `contracts/` | App Manifest + Host API — the wire contract, with golden tests |
| `conformance/` | black-box suite every provider must pass |
| `providers/local` | reference Host API over a local `barista.sh` node agent |
| `sdks/python` | provider selection, capability negotiation, streaming |
| `apps/` | Factory, Lift, Story, and the Pi/Claude/Codex adapters |
| `openspec/` | proposals, designs, ratified specs |

## Commands

```sh
openspec validate --all --strict            # every active change
( cd contracts/tests && uv run pytest -q )  # and the same per package:
( cd conformance && uv run --extra test pytest -q )
( cd providers/local && uv run --extra test pytest -q )
( cd sdks/python && uv run --extra test pytest -q )
( cd apps/factory && uv run --extra test pytest -q )
uv run --with 'jsonschema[format]>=4.20' python scripts/supply_chain_check.py
```

There is no repo-root test run — `cd` into the package you changed. The
supply-chain check (digests, references, locks, schema drift) is a real gate,
not a lint.

## Everything runs offline

Every CI job is marked *offline*, and the conformance harness treats a
proprietary import, credential, or network dependency in the standalone profile
as a **failing build, not a warning**. A test that needs the network is a test
that cannot run here. This is also why the reference doubles matter: a provider
double that lies (keeps an old secret working after rotation, reads scope from
the request) must be *caught* by the suite — write the dishonest double and
prove it fails before trusting a test that passes.

## What the contract may and may not say

- **No harness-specific field** in `contracts/host-api` or
  `contracts/app-manifest`. Nothing Pi-, Claude- or Codex-shaped. It goes in the
  adapter package. A contract that names one vendor's harness has stopped being
  portable.
- **Secret *references*, never plaintext secrets**, in manifests and stories.
- **No arbitrary numbers in a portable contract.** A refresh chain is bounded by
  the session, not by a number someone picked — if a bound cannot be derived from
  something the contract already names, it probably does not belong in the
  contract.
- **A capability profile must have a live operation behind it.** Advertising a
  profile and answering 501 is a conformance failure on both sides. Prefer
  extending an existing profile id over inventing a vendor extension: an app
  should check one thing, and `grants.delegated` covering refresh is the worked
  example of collapsing two checks into one.

## Specs

`MODIFIED` requirements must restate ratified text **verbatim** — verified
programmatically, so approximate quoting fails the gate. Task checkboxes are a
claim about code: tick them against the code, not a PR description, and leave a
task unchecked with a reason rather than ticking it optimistically. "Not in this
change" sections are bullets, not checkboxes — a non-goal can never honestly be
ticked.

When a design document asserts what a provider does, **verify it against that
provider**. A design here once claimed the reference provider revoked grants on
session deletion; it did not, and the claim was load-bearing for a security
argument. The fix was to make it an explicit obligation in the OpenAPI plus a
golden test, which turned an assumption into a requirement.

## Factory

`apps/factory` is the coordinator/worker fan-out app, and the one most likely to
drift from its own spec. Two divergences currently stand, both worth knowing
before extending it:

- `openspec/specs/factory-app/spec.md` ratifies persistence of a **task graph**,
  but no dependency edges exist anywhere — `mission.schema.json` task properties
  are `check, collect, command, env, id, prompt, workdir`, and the coordinator is
  a flat fan-out bounded by `concurrency`.
- The same spec says Factory "SHALL not assume a specific agent adapter", while
  the worker image bakes one agent and the orchestrator shells it directly.

Neither is a reason to stop; both are reasons not to build on the assumption
that the spec describes the code.
