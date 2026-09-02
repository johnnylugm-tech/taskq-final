"""[FR-01, FR-02, FR-05, FR-09] L3 — use cases / business logic.

Submodules are re-exported here so callers (and ``monkeypatch.setattr``
in tests) can address them by attribute on the package — e.g.
``taskq_api.service.health.is_database_ready`` — rather than reaching
into the submodule explicitly. The package and its submodules are the
same module object; the alias is a convenience, not a new surface.

Citations:
    - SPEC.md §3 FR-01 (CRUD use cases)
    - SPEC.md §3 FR-05 (rate-limit admission)
    - SPEC.md §3 FR-09 (health probes)
    - SAD.md §2.7
"""

from taskq_api.service import health  # noqa: F401  -- re-export for attribute access
from taskq_api.service import ratelimit  # noqa: F401  -- re-export so monkeypatch can target ``service.ratelimit.try_consume`` (FR-10 AC-10.5)
from taskq_api.service import tasks  # noqa: F401  -- re-export so monkeypatch can target ``service.tasks.list_tasks`` (FR-10 AC-10.3)
