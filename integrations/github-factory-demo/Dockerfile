FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /src
COPY sdks/python /src/sdks/python
COPY integrations/github-factory-demo /src/integrations/github-factory-demo
RUN pip install /src/sdks/python /src/integrations/github-factory-demo

RUN useradd --create-home --uid 10001 barista \
 && mkdir -p /state/results \
 && chown -R barista:barista /state
USER 10001:10001
WORKDIR /state

ENV BARISTA_GITHUB_DEMO_DB=/state/github-factory-demo.sqlite3 \
    BARISTA_GITHUB_DEMO_RESULTS=/state/results
EXPOSE 8098
CMD ["barista-github-demo", "serve", "--host", "0.0.0.0", "--port", "8098"]
