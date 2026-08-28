# Running typed app operations

`barista-app run` is the provider-neutral OSS runner for operations declared in
an App Manifest. A managed CLI can project the same command as `barista run`;
the wire shape remains the canonical App Run envelope.

Set only provider configuration:

```sh
export BARISTA_HOST_API_ENDPOINT=http://127.0.0.1:8088
# Managed providers may also require BARISTA_HOST_API_TOKEN.
```

Then run an installed app:

```sh
barista-app run \
  --app reviewer@1.0.0 \
  --operation review \
  --input review.json \
  --bind 'workspace={"kind":"sh.barista.git.repository","uri":"file:///src/project","ref":"main"}' \
  --output result.json
```

`--operation` is optional when the manifest declares exactly one operation. The
run name is deterministic from the exact app identity, operation, input, and
bindings unless `--name` is supplied, so a retry converges on the same owning
session.

Generic flags compile directly into one envelope:

- `--bind NAME=JSON` supplies a manifest-declared named binding;
- `--secret NAME=REFERENCE` supplies a reference such as `secret://...`, never a
  raw credential;
- `--deliver NAME=JSON` explicitly requests a manifest-declared side effect;
- `--emit-envelope PATH` writes the exact canonical launch bytes;
- `--detach` returns after idempotent launch;
- `--cleanup` deletes the owning session only after a terminal result has been
  collected, verified, and persisted.

`--detach` and `--cleanup` cannot be combined. Collection, digest, schema, or
persistence failure always leaves the owning session intact for bounded
forensics. Terminal job/coordinator entrypoints keep a bounded 120-second
handoff window after registering their result, because managed microVMs stop
when PID 1 exits and result collection reads canonical bytes through an owning-
session exec. Cleanup may end that window earlier.

A local app manifest can be selected by path. A clean Git revision is recorded;
a dirty source is refused unless `--development` is explicit. Development mode
does not relax the requirement that the manifest's OCI workload is digest
pinned.

```sh
barista-app run --app ./apps/reviewer --input review.json --detach
```

Convenience flags are projections, not another protocol. For a manifest that
declares the corresponding `workspace`, `objective`, and `change` slots:

```sh
barista-app run \
  --app factory@0.1.0 \
  --mission mission.json \
  --repo https://github.com/acme/project.git \
  --repo-ref main \
  --issue https://github.com/acme/project/issues/7 \
  --secret forge=secret://forge/token \
  --publish draft-pr \
  --publish-credential forge \
  --head-branch barista/issue-7
```

This compiles to `bindings.workspace`, `bindings.objective`, and the explicit
`deliveries.change`; issue text cannot create that delivery or alter its target.

A remote **app-source** repository must select an exact full commit and manifest
path:

```sh
barista-app run \
  --app 'git+https://github.com/acme/apps.git#0123456789abcdef0123456789abcdef01234567:apps/reviewer/manifest.json' \
  --input review.json --detach
```

The resolver fetches only that immutable commit, reads and validates the
manifest with `git show`, and records both manifest/workload identity and source
revision before any provider mutation. It never checks out or executes app
source and never builds it implicitly. URL credentials and mutable branch/tag
names are refused. Building from modified local app source remains explicit
`--development` behavior and still requires a digest-pinned workload manifest.

Git repositories supplied as **project bindings** are different: the source
adapter resolves their ref once, checks out the exact commit, applies an
explicit size bound, and refuses submodule or LFS behavior unless the binding
chooses an implemented policy.
