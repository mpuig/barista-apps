FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /src
COPY apps/github-product-worker/pyproject.toml /src/apps/github-product-worker/
COPY apps/github-product-worker/barista_app_github_product_worker /src/apps/github-product-worker/barista_app_github_product_worker
RUN pip install --no-deps /src/apps/github-product-worker \
 && test -x /usr/local/bin/barista-demo-brd-author \
 && test -x /usr/local/bin/barista-demo-feature-planner \
 && test -x /usr/local/bin/barista-demo-feature-worker

RUN mkdir -p /work
WORKDIR /work
CMD ["/bin/sh", "-c", "sleep infinity"]
