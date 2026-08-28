FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /src
COPY apps/github-issue-triage/pyproject.toml /src/apps/github-issue-triage/
COPY apps/github-issue-triage/barista_app_github_issue_triage /src/apps/github-issue-triage/barista_app_github_issue_triage
RUN pip install --no-deps /src/apps/github-issue-triage \
 && test -x /usr/local/bin/barista-demo-issue-triage

RUN mkdir -p /work
WORKDIR /work
CMD ["/bin/sh", "-c", "sleep infinity"]
