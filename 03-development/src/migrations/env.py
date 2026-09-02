"""[FR-07] Alembic environment script.

Reads ``TASKQ_DB_URL`` from :mod:`taskq_api.config` and supports both
``online`` (live engine connection) and ``offline`` (SQL generation)
modes.

The module intentionally avoids any project-internal helper that
imports ``sqlalchemy`` — the only such layer in this codebase is
:mod:`taskq_api.repository`, and alembic's runtime is part of the
build/test pipeline, not the API request path (NFR-06 layering).

The runtime ``alembic.context`` proxy is imported lazily so that this
module can also be imported by the FR-07 test suite (which checks the
canonical module set is present) without needing the alembic runtime
proxy to be initialised.

Citations:
    - SPEC.md §5.1 (env-var contract)
    - SPEC.md §5.2 (schema)
    - SAD.md §3.4 (revision chain)
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

# Make ``taskq_api`` importable when alembic is invoked via
# ``python -m alembic`` from a project root whose PYTHONPATH does not
# already contain ``03-development/src``.
_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_SRC = _THIS_DIR.parent
if str(_PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(_PROJECT_SRC))

from taskq_api.config import Settings  # noqa: E402  -- import after sys.path fix-up


def _resolve_alembic_context():
    """Return the ``alembic.context`` runtime proxy.

    The proxy is only initialised when alembic invokes ``env.py`` as
    part of ``alembic upgrade`` / ``alembic downgrade`` / ``alembic
    upgrade --sql``. Calling ``context.config`` outside that runtime
    raises ``AttributeError``; callers should defer the lookup to
    inside ``run_migrations_online`` / ``run_migrations_offline``.
    """
    from alembic import context as alembic_context

    return alembic_context


def _alembic_config():
    """Return the alembic ``Config`` object for the current run.

    Raises ``AttributeError`` outside the alembic runtime proxy (used
    here only by the online / offline migration entry points).
    """
    return _resolve_alembic_context().config


# Configure Python logging from alembic.ini when alembic invokes
# env.py with a config_file_name (the default ``alembic`` invocation
# always provides one). This block runs only when the proxy is live,
# so it is guarded.
def _configure_logging_if_available():
    try:
        cfg = _alembic_config()
    except AttributeError:
        return
    if cfg.config_file_name:
        fileConfig(cfg.config_file_name)


_configure_logging_if_available()

# Resolve the DSN from the canonical settings module (never hard-coded
# in alembic.ini per NFR-04). ``TASKQ_DB_URL`` overrides whatever is in
# the environment so the FR-07 subprocess tests can target a
# ``tmp_path``-backed SQLite file.
_settings = Settings()  # reads TASKQ_DB_URL

# Metadata target — ``None`` disables autogenerate but lets the online
# mode open a connection against the DSN. The three revision scripts
# in :mod:`migrations.versions` are hand-written, so no autogenerate
# is needed at runtime.
target_metadata = None


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without a live DB).

    The DSN is read from ``sqlalchemy.url`` (set to
    ``Settings.db_url`` below). The SQL emitted is what ``alembic
    upgrade head --sql`` prints — verified by FR-07 AC-7.7.
    """
    alembic_context = _resolve_alembic_context()
    cfg = alembic_context.config
    cfg.set_main_option("sqlalchemy.url", _settings.db_url)
    url = cfg.get_main_option("sqlalchemy.url")
    alembic_context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with alembic_context.begin_transaction():
        alembic_context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (open a live DB connection)."""
    alembic_context = _resolve_alembic_context()
    cfg = alembic_context.config
    cfg.set_main_option("sqlalchemy.url", _settings.db_url)
    connectable = engine_from_config(
        cfg.get_section(cfg.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        alembic_context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with connection.begin():
            alembic_context.run_migrations()


# Wire the entry points — alembic's env.py contract is that the proxy's
# ``run_migrations`` symbol resolves to a function that dispatches
# between online and offline modes. The default template invokes those
# two helpers via a top-level ``if`` block; we mirror that here.
#
# The dispatch is wrapped in a ``try/except NameError`` because the
# alembic runtime proxy raises ``NameError`` when ``env.py`` is
# imported outside an alembic invocation (e.g. by the FR-07 test
# suite, which imports ``migrations.env`` to confirm the module is
# present on disk). Skipping the dispatch outside alembic is safe —
# the ``run_migrations_online`` / ``run_migrations_offline`` entry
# points are the only callers that matter at runtime.
def _dispatch() -> None:
    alembic_context = _resolve_alembic_context()
    try:
        is_offline = alembic_context.is_offline_mode()
    except NameError:
        # Proxy not established — we are NOT inside an alembic run
        # (this branch fires only when env.py is imported as a regular
        # module by the FR-07 test suite). Skip the dispatch; alembic
        # will invoke the right helper when env.py is loaded by
        # ``alembic upgrade`` / ``alembic downgrade``.
        return
    if is_offline:
        run_migrations_offline()
    else:
        run_migrations_online()


_dispatch()