# taskq-api project Makefile (NFR-12).
#
# `make verify-system` exercises the full migration round-trip + full tests
# + service smoke path; it MUST exit 0 and print `verify-system: PASS`
# (SPEC.md §4 acceptance #27).

PYTHON     ?= /Users/johnny/projects/taskq-final/.venv/bin/python
PROJECT_SRC := 03-development/src
# NFR-12 (verifiability): only export PYTHONPATH for ``alembic`` /
# ``taskq_api.__main__`` invocations. Setting it globally also affects
# ``pytest``, where it causes the conftest at ``tests/conftest.py`` to
# observe two ``03-development/src`` entries on ``sys.path`` and
# ``tests/test_fr07.py`` to fail with masked import resolution
# (six v3 migration tests break because ``migrations`` is picked
# from the wrong entry). The pattern below keeps the alembic
# subprocess path clean (it MUST find ``taskq_api``) without
# poisoning the pytest parent process.
ALEMBIC_ENV := PYTHONPATH=$(PROJECT_SRC):$$PYTHONPATH

.PHONY: verify-system test lint migrations-up migrations-down migrations-roundtrip

# NFR-12: full system verification — migration upgrade, full tests, health/
# ready smoke, then downgrade base + upgrade head round-trip.
verify-system:
	@echo "==> full test suite (clean taskq.db)"
	@rm -f taskq.db
	$(PYTHON) -m pytest --ignore=harness/tests
	@echo "==> alembic upgrade head"
	@rm -f taskq.db
	$(ALEMBIC_ENV) $(PYTHON) -m alembic upgrade head
	@echo "==> service smoke (/healthz, /readyz)"
	$(ALEMBIC_ENV) $(PYTHON) -m taskq_api.__main__ healthcheck
	@echo "==> downgrade base + upgrade head round-trip"
	$(ALEMBIC_ENV) $(PYTHON) -m alembic downgrade base
	$(ALEMBIC_ENV) $(PYTHON) -m alembic upgrade head
	@echo "verify-system: PASS"

test:
	$(PYTHON) -m pytest --ignore=harness/tests

lint:
	$(PYTHON) -m ruff check 03-development/src/

migrations-up:
	$(ALEMBIC_ENV) $(PYTHON) -m alembic upgrade head

migrations-down:
	$(ALEMBIC_ENV) $(PYTHON) -m alembic downgrade base

migrations-roundtrip:
	$(ALEMBIC_ENV) $(PYTHON) -m alembic upgrade head
	$(ALEMBIC_ENV) $(PYTHON) -m alembic downgrade base
	$(ALEMBIC_ENV) $(PYTHON) -m alembic upgrade head