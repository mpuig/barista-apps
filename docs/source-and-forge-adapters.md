# Source and forge adapters

App Run bindings describe resources; they do not grant authority and providers
do not interpret app-specific binding kinds. The SDK's source and forge adapters
provide shared behavior for repository apps without adding Git or GitHub fields
to the Host API.

## Git repository bindings

`sh.barista.git.repository` resolution follows four steps:

1. resolve the requested ref exactly once;
2. record the selected commit;
3. clone and check out that commit detached;
4. measure the materialized tree against a caller-supplied byte limit.

If a ref moves between steps 1 and 3 and the selected commit is no longer
available, acquisition fails. It never substitutes the new tip. The destination
must not already exist, and failure removes only the destination created by the
adapter.

Submodules and Git LFS default to `reject`. `options.submodules="ignore"` leaves
submodules uninitialized; `options.lfs="ignore"` retains pointer files. Checkout
behavior is therefore explicit and cannot silently acquire an unbounded second
source graph.

Private HTTP(S) repositories may be supplied an already resolved credential by
the trusted runner. The adapter passes it through a mode-0700 `GIT_ASKPASS`
helper and environment, never in an argument, URI, result, or error. The App Run
envelope itself contains only the credential alias and its `secret://`,
`vault://`, or other provider-resolvable reference.

## Objectives

Local `sh.barista.text` and `sh.barista.specification` objectives are bounded,
UTF-8, and content addressed. Forge issue adapters return title/body/state as
**untrusted objective content** plus immutable revision provenance. Their output
has no fields for credentials, egress, budget, repository scope, acceptance
policy, or delivery. Text asking for broader authority remains text.

## Patch, branch, and draft-change output

`create_workspace_patch` creates a binary-capable Git patch including new and
deleted files, restores the index, applies a byte bound, and rejects
high-confidence secret shapes. `commit_workspace_branch` is an explicit local
side effect: it first verifies that workspace HEAD is still the resolved base,
then creates and commits a caller-named branch with hooks disabled.

External publication requires a `DeliveryRequest`. `deliver_draft_change`
refuses unless all of these remain true:

- the result state is `succeeded`;
- the adapter supports the declared delivery kind;
- the delivery target exactly equals the bound repository;
- the forge's current base ref still equals the commit acquired for the run;
- the head branch is explicitly valid;
- title/body pass secret-shape checks.

`GitHubForge` is the concrete GitHub adapter. It resolves issue content and refs
through the versioned API, applies the verified patch to the exact base in a
temporary checkout, creates a deterministic head commit, pushes that head, and
creates a draft pull request. Its token is sent only as an HTTP authorization
header or through a mode-0700 `GIT_ASKPASS` helper and never appears in a URL or
argv. Retries verify an existing branch, draft state, base, head, and embedded
patch-digest marker instead of silently replacing conflicting content.

`FakeForge` implements the same issue/ref/draft shape entirely offline. Repeating
the same repository/branch/base/patch returns the existing draft; changing
content under an existing branch is a conflict, never a duplicate pull request.
Both adapters return the exact resulting head commit in the canonical output.
