FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml requirements-test.txt /app/
COPY src/ /app/src/
COPY ledger/ /app/ledger/
COPY tests/ /app/tests/
COPY test/ /app/test/
COPY config.yaml .env.example /app/

RUN pip install --no-cache-dir -r requirements-test.txt && pip install --no-cache-dir /app

# Rootless-friendly: no privileged ops; writable HOME for git/pytest caches
ENV HOME=/tmp PYTHONDONTWRITEBYTECODE=1
USER 65534:65534

CMD ["pytest", "-q", "-p", "no:cacheprovider"]
