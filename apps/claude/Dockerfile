# Session-safe Claude Code workload. Build from the repository root.
# The base index and npm package version are immutable inputs; the published
# workload is additionally selected by the digest recorded in manifest.json.
FROM node:22-bookworm-slim@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5

ARG VCS_REF="unknown"
LABEL org.opencontainers.image.source="https://github.com/mpuig/barista-apps" \
      org.opencontainers.image.revision="$VCS_REF" \
      org.opencontainers.image.title="Barista Claude Code" \
      org.opencontainers.image.version="2.1.251"

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates git ripgrep \
 && rm -rf /var/lib/apt/lists/* \
 && npm install --global --omit=dev @anthropic-ai/claude-code@2.1.251 \
 && npm cache clean --force \
 && test "$(claude --version | awk '{print $1}')" = "2.1.251"

RUN install -d -o node -g node -m 0750 /work
USER node
WORKDIR /work

# A Host API session is a durable execution environment. Keep PID 1 alive;
# individual Claude calls run through session.exec rather than making a
# one-shot CLI process the lifetime of the session.
ENTRYPOINT ["/usr/bin/sleep", "infinity"]
