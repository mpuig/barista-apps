FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /src
COPY sdks/python /src/sdks/python
RUN pip install /src/sdks/python

COPY apps/change-agent/pyproject.toml apps/change-agent/README.md /src/apps/change-agent/
COPY apps/change-agent/barista_app_change_agent /src/apps/change-agent/barista_app_change_agent
RUN pip install --no-deps /src/apps/change-agent \
 && test -x /usr/local/bin/barista-change-agent

RUN mkdir -p /work/app-runs
WORKDIR /work
CMD ["/usr/local/bin/barista-change-agent"]
