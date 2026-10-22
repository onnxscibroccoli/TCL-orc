# CI validation image only; no production ceremony or custody keys.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}" PYTHONDONTWRITEBYTECODE=1 npm_config_cache=/tmp/npm
WORKDIR /opt/protocol
COPY . .
RUN pip install --no-cache-dir -r requirements-production.txt -r verification/requirements.txt -r requirements-test.txt && npm ci
ARG SOURCE_REVISION
ARG SOURCE_ARCHIVE_SHA256
LABEL org.opencontainers.image.revision=${SOURCE_REVISION} tcl.source.archive.sha256=${SOURCE_ARCHIVE_SHA256}
USER 65532:65532
CMD ["bash", "/opt/protocol/production/validate-in-container.sh"]
