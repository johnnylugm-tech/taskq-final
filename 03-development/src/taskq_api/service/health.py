"""[FR-09] Service-layer health checks — L1 (service).

The readiness probe asks the service whether the database is reachable;
the service forwards the call down to the repository so the HTTP handler
in ``api/health.py`` never crosses layers.

Citations:
    - SPEC.md §3 FR-09 (liveness / readiness)
    - SAD.md §2.7 (layer boundaries)
"""

from __future__ import annotations

from taskq_api.repository.session import ping


def is_database_ready() -> bool:
    """[FR-09] Return ``True`` iff the database engine answers ``SELECT 1``.

    This is the public service-layer entry point for the readiness probe;
    ``api.health.readyz`` calls this rather than reaching into the
    repository directly, preserving the ``api > service > repository``
    layer order.
    """
    return ping()
