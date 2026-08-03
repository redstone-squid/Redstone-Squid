# Creating a database migration

Prerequisites:

- Configure `SQUID_DATABASE_URL` for a PostgreSQL 15+ database with pgvector installed.
- Upgrade your development database with `just db-upgrade`.

Steps:

1. Update the SQLAlchemy models in `squid/<context>/infrastructure/models.py`, then register the module in
   `squid/persistence/__init__.py`. A new models module that is not registered there is invisible to
   `Base.metadata`, so `alembic autogenerate` emits a *drop* for its tables rather than a create.
2. For a PostgreSQL function or trigger, update `squid/db/postgres_entities.sql`.
3. Run `just db-revision "<short description>"`.
4. Review the generated revision. Data migrations and PostgreSQL procedures require explicit `op.execute(...)` SQL.
5. Run `just db-upgrade`, then `just db-check`.
6. Run `just test` and `just test-integration`.
7. Deploy the revision with `alembic upgrade head`.

`alembic-utils` declaratively compares the functions and triggers listed in `postgres_entities.sql`. The three
PostgreSQL procedures captured by the baseline are not supported by `alembic-utils`; change those with explicit SQL in
a normal Alembic revision.

## Adopting the existing Supabase database

The frozen baseline matches the remote schema through Supabase migration `20260330091500`. The repository's former
`20260728090000_vote_session_options.sql` migration is represented by the next Alembic revision.

After verifying a backup and confirming the target matches the baseline:

```console
alembic stamp 20260728_baseline
alembic upgrade head
alembic check
```

Stamping changes migration metadata without applying schema SQL. Never stamp an unverified database, and do not run
`supabase db push` for new application migrations after the cutover.
