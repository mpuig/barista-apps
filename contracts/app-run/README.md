# Barista App Run contract

An App Run is the canonical, provider-neutral input and result protocol for one
operation declared by an App Manifest. It composes existing Host API resources;
it is not a second provider scheduler.

- Run envelope: [`v1alpha1/schema.json`](v1alpha1/schema.json)
- Run result: [`v1alpha1/result.schema.json`](v1alpha1/result.schema.json)
- Media types:
  - `application/vnd.barista.app-run.v1alpha1+json`
  - `application/vnd.barista.app-run-result.v1alpha1+json`

## The three identities

Do not collapse these:

1. **App source** is where a manifest was discovered, such as an installed name,
   local checkout, or Git repository at an exact revision.
2. **Project bindings** are resources supplied to the operation, such as a Git
   repository, Barista session, dataset, artifact, or forge issue.
3. **Executable identity** is the immutable OCI digest in the validated App
   Manifest. An app-source repository is never executed merely because it was
   passed to a runner.

A result records the exact app source revision when one exists, the manifest and
workload identities, and immutable binding identities such as a resolved Git
commit.

## Inputs, bindings, secrets, and deliveries

They are deliberately separate:

- `input` is small app-specific JSON with a declared media type;
- `bindings` are typed references to external resources;
- `secrets` contain provider-resolvable references, never values;
- `deliveries` explicitly request output or an external side effect.

Binding and delivery `kind` values are namespaced and opaque to Host API
providers. A GitHub adapter can understand `com.github.issue` without adding a
GitHub field to the Host API.

A repository binding does not imply permission to publish. Creating a branch,
pull request, or other external update requires an explicitly requested delivery
that the selected manifest operation declares.

## Convenience CLIs

A CLI may project friendly options into the canonical envelope:

```text
--repo URL          bindings.workspace
--issue URL         bindings.objective
--publish draft-pr  deliveries.change
```

Those flags are not a second wire contract. The resulting canonical envelope is
what is validated, serialized, delivered, and recorded.
