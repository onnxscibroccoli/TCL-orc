# Build with --build-arg BASE_IMAGE=python:3.12-slim@sha256:<verified digest>.
# Push and resolve the resulting OCI digest before approving it in external policy.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
USER 65532:65532
WORKDIR /workspace
ENTRYPOINT ["python3"]
