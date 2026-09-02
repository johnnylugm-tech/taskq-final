# taskq-api project Makefile (NFR-12).
#
# `make verify-system` exercises the full migration round-trip + full tests
# + service smoke path; it MUST exit 0 and print `verify-system: PASS`
# (SPEC.md §4 acceptance #27).

PYTHON     ?= python3

.PHONY: verify-system test lint migrations-up migrations-down migrations-roundtrip

# NFR-12: full system verification — migration upgrade, full tests, health/
# ready smoke, then downgrade base + upgrade head round-trip.
verify-system:
	@echo "==> alembic upgrade head"
	$(PYTHON) -m alembic upgrade head
	@echo "==> full test suite"
	$(PYTHON) -m pytest
	@echo "==> service smoke (/healthz, /readyz)"
	$(PYTHON) -m taskq_api.__main__ healthcheck
	@echo "==> downgrade base + upgrade head round-trip"
	$(PYTHON) -m alembic downgrade base
	$(PYTHON) -m alembic upgrade head
	@echo "verify-system: PASS"

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check 03-development/src/

migrations-up:
	$(PYTHON) -m alembic upgrade head

migrations-down:
	$(PYTHON) -m alembic downgrade base

migrations-roundtrip:
	$(PYTHON) -m alembic upgrade head
	$(PYTHON) -m alembic downgrade base
	$(PYTHON) -m alembic upgrade head