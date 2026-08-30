## 1. Projection model

- [x] 1.1 Add durable desired/published activity projection state and monotonic revisions.
- [x] 1.2 Map programs into bounded generic streams with stable events, links, artifacts, and canonical phases.
- [x] 1.3 Keep activity delivery corrective, serialized, startup-recoverable, and non-blocking.

## 2. Authority

- [x] 2.1 Add separate endpoint and token configuration without defaults to Host API or forge credentials.
- [x] 2.2 Refuse equal activity, Host API, forge, or Project credential values.
- [x] 2.3 Keep Cloud action state inert; execute only through a fixed source-side adapter with durable request identity and bounded I/O.

## 3. Verification

- [x] 3.1 Test deterministic mapping, bounds, retry convergence, stale state, projection failure isolation, and source-side action execution.
- [x] 3.2 Mutation-test that activity fields cannot advance programs and projection failure cannot fail accepted work.
- [x] 3.3 Run controller, Factory, contract, standalone, supply-chain, and strict OpenSpec checks.
- [x] 3.4 Record managed per-user projection, explicit deployment request, source settlement, generated endpoint, and responsive UI evidence through the generic activity API.
