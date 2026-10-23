# syntax=docker/dockerfile:1
# Multi-stage TCL image (works with podman and docker).
#   toolchain : circom 2.1.9 + snarkjs 0.7.6 (node 20) + foundry (forge/cast/anvil) + git
#   app       : toolchain + installed `tcl` package, non-root; default = daemon w/ HTTP /health
#   test      : app + `pytest` with ZK/EVM tools required (fail-closed). Default (last) stage.
ARG CIRCOM_VERSION=v2.1.9
ARG FOUNDRY_VERSION=v1.8.6

FROM node:20-bookworm-slim AS node-deps
WORKDIR /deps
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM python:3.12-slim-bookworm AS toolchain
ARG CIRCOM_VERSION
ARG FOUNDRY_VERSION
RUN apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=node-deps /usr/local/bin/node /usr/local/bin/node
COPY --from=node-deps /deps/node_modules /opt/tcl/node_modules
RUN curl -sSfL -o /usr/local/bin/circom \
      "https://github.com/iden3/circom/releases/download/${CIRCOM_VERSION}/circom-linux-amd64" \
    && chmod 0755 /usr/local/bin/circom \
    && curl -sSfL "https://github.com/foundry-rs/foundry/releases/download/${FOUNDRY_VERSION}/foundry_${FOUNDRY_VERSION}_linux_amd64.tar.gz" \
       | tar -xz -C /usr/local/bin forge cast anvil \
    && circom --version && forge --version && anvil --version
ENV PATH=/opt/tcl/node_modules/.bin:$PATH \
    TCL_CIRCOMLIB=/opt/tcl/node_modules \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN snarkjs --version 2>&1 | head -1 || true

FROM toolchain AS app
RUN useradd --uid 10001 --create-home --home-dir /home/tcl tcl
WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir -r requirements-test.txt && pip install --no-cache-dir -e /app \
    && ln -s /opt/tcl/node_modules /app/node_modules \
    && chown -R tcl:tcl /app /home/tcl
USER 10001:10001
ENV HOME=/home/tcl
# pre-fetch solc 0.8.24 and compile once so runtime needs no network
RUN forge build && chmod -R a+rwX /app /home/tcl
EXPOSE 8787
CMD ["tcl", "daemon", "start", "--http-host", "0.0.0.0", "--http-port", "8787", \
     "--socket", "/tmp/tcl/daemon.sock", "--tasks-db", "/tmp/tcl/tasks.db"]

FROM app AS test
ENV TCL_REQUIRE_ZK=1 TCL_REQUIRE_CHAIN=1
CMD ["pytest", "-q", "-p", "no:cacheprovider"]
