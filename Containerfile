# CI validation only; both input images must be supplied by immutable digest.
ARG BASE_IMAGE
ARG PYTHON_IMAGE
FROM ${BASE_IMAGE} AS node_tools
FROM ${PYTHON_IMAGE}
COPY --from=node_tools /usr/local/bin/node /usr/local/bin/node
COPY --from=node_tools /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && apt-get update && apt-get install -y --no-install-recommends git ca-certificates libstdc++6 chromium \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}" PYTHONDONTWRITEBYTECODE=1 npm_config_cache=/tmp/npm TCL_CHROMIUM=/usr/bin/chromium
WORKDIR /opt/protocol
COPY . .
RUN pip install --no-cache-dir -r requirements-production.txt -r verification/requirements.txt -r requirements-test.txt && npm ci
ARG SOURCE_REVISION
ARG SOURCE_ARCHIVE_SHA256
LABEL org.opencontainers.image.revision=${SOURCE_REVISION} tcl.source.archive.sha256=${SOURCE_ARCHIVE_SHA256}
USER 65532:65532
CMD ["bash", "/opt/protocol/production/validate-in-container.sh"]
