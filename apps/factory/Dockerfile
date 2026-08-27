# The Factory coordinator's workload image.
#
# `manifest.json` has named an image since the app was written, and no image
# existed — so Factory could be tested but never *installed*, which is the one
# thing an app has to be. This is that image.
#
# Build from the repository root, not from this directory: the app depends on
# `sdks/python` through a path source, so the SDK has to be inside the context.
#
#   docker build -f apps/factory/Dockerfile -t <ref> .
#
# Deliberately not multi-stage. The install is two pure-Python packages and
# their wheels; a builder stage would save little and cost the ability to
# `pip install -e` a fix into a running container while debugging a mission,
# which for a coordinator that may run for an hour is worth more.

FROM python:3.12-slim

# git and ca-certificates because a coordinator's *tasks* routinely clone and
# push, and the coordinator itself talks HTTPS to the Host API. Nothing else:
# the coordinator drives the provider over the network and does no local build.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /src

# The SDK first and on its own layer: it changes far less often than the
# coordinator, so a coordinator edit does not reinstall httpx.
COPY sdks/python /src/sdks/python
RUN pip install /src/sdks/python

COPY apps/factory/pyproject.toml apps/factory/README.md /src/apps/factory/
# `mission.schema.json` lives inside the package and is copied by this line. It
# used to sit one level up and be copied separately, which is exactly how it
# ended up absent from the wheel.
COPY apps/factory/barista_app_factory /src/apps/factory/barista_app_factory
# `--no-deps` for the path source it already has installed above, then the one
# remaining runtime dependency explicitly. Installing the app plainly would make
# pip try to resolve `barista-app-sdk` from an index, where it does not exist.
RUN pip install --no-deps /src/apps/factory \
 && pip install 'jsonschema[format]>=4.20'

# `manifest.json`'s entrypoint is the absolute path `/usr/local/bin/barista-factory`.
# pip already installs the console script there, so this asserts it rather than
# creating it — a manifest naming a path the image does not have is a session
# that fails at start with no useful message, and the build is where that should
# be caught. `--help` also proves the package imports, so a missing runtime
# dependency fails the build instead of the first mission.
RUN test -x /usr/local/bin/barista-factory \
 && /usr/local/bin/barista-factory --help >/dev/null

# Where a mission and its state live. `manifest.json` sets working_dir to /work,
# and the entrypoint reads /work/mission.json from it.
RUN mkdir -p /work
WORKDIR /work

# Run the coordinator. `__main__.py` prints the readiness line the manifest's
# probe watches for, immediately before it starts the mission — so readiness
# means "the coordinator is up and about to work", not "a shell is asleep".
#
# This replaces a default of `sh -c "echo 'coordinator ready'; exec sleep
# infinity"`, which existed so a session could become ready without a mission
# and be driven from outside by exec. That was a second way to run Factory, and
# keeping both meant the published image never ran the app it packages: every
# session booted into a sleep, and `docker run` did something different from a
# Host API session. One story now — the app runs its mission — and the two
# agree.
#
# The mission arrives in `$BARISTA_FACTORY_MISSION` (apps-005 D1). No argument
# is given here, because a path argument that is present and unreadable is an
# error rather than a fallback, and nothing writes /work/mission.json.
CMD ["/usr/local/bin/barista-factory", "run"]
