# Offline-first entry points (see docs/OFFLINE.md). No target here touches the network.
PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

.PHONY: offline offline-verify offline-runtime no-ethereum offline-test

offline: offline-verify
	$(PYTHON) scripts/offline_install.py --python $(PYTHON)

offline-runtime: offline-verify
	$(PYTHON) scripts/offline_install.py --python $(PYTHON) --runtime-only

offline-verify:
	python3 scripts/bundle_verify.py

no-ethereum:
	python3 scripts/check_no_ethereum.py
	python3 scripts/gen_poseidon_constants.py --check

offline-test: offline
	$(PYTHON) -m pytest -q
