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
forensics.

A local app manifest can be selected by path. A clean Git revision is recorded;
a dirty source is refused unless `--development` is explicit. Development mode
does not relax the requirement that the manifest's OCI workload is digest
pinned.

```sh
barista-app run --app ./apps/reviewer --input review.json --detach
```

Remote app repositories are not cloned or executed implicitly. Until exact
remote source resolution is implemented, install a validated pinned manifest or
use a local manifest source.
