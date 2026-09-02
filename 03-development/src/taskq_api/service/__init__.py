"""L3 — use cases / business logic (no Session, no sqlalchemy).

Submodules are re-exported here so callers (and ``monkeypatch.setattr``
in tests) can address them by attribute on the package — e.g.
``taskq_api.service.health.is_database_ready`` — rather than reaching
into the submodule explicitly. The package and its submodules are the
same module object; the alias is a convenience, not a new surface.

Citations: SAD.md §2.7.
"""

from taskq_api.service import health  # noqa: F401  -- re-export for attribute access
