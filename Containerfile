FROM python:3.12-slim

WORKDIR /app
COPY ledger/ /app/ledger/
COPY requirements-test.txt /app/

RUN pip install --no-cache-dir -r requirements-test.txt

# Rootless-friendly: no privileged ops
USER 65534:65534

CMD ["pytest", "-q", "ledger/test_demurrage.py"]
