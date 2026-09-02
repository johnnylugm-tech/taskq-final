"""[FR-07] Alembic migration package for taskq-api.

Three revisions live under :mod:`migrations.versions`:

    - :mod:`v1_initial` — ``tasks`` (with ``result_json``) and ``api_keys``
    - :mod:`v2_tags`    — ``tags`` + ``task_tags`` M2M + UNIQUE index on
                           ``tasks.name``
    - :mod:`v3_split_results` — split ``tasks.result_json`` rows into the
                           independent ``task_results`` table (and reverse)

Citations:
    - SPEC.md §5.2 (database schema)
    - SAD.md §3.4 (revision chain)
"""