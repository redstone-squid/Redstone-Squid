# Creating a database migration

Prerequisites:

- Configure `SQUID_DATABASE_URL` for a PostgreSQL 15+ database with pgvector installed.
- Upgrade your development database with `just db-upgrade`.

Steps:

1. Update the SQLAlchemy models in `squid/<context>/infrastructure/models.py`, then register the module in
   `squid/persistence/__init__.py`. A new models module that is not registered there is invisible to
   `Base.metadata`, so `alembic autogenerate` emits a *drop* for its tables rather than a create.
2. For a PostgreSQL function or trigger, update `squid/persistence/postgres_entities.sql`.
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

## Never edit a revision that has already been applied

`alembic_version` holds a pointer, not a history, so a database cannot tell you what it ran to get
where it is. `alembic_migration_history` is an append-only ledger beside it: one row per migration
step, written from an `on_version_apply` callback inside the migration transaction, recording the
revision, the direction, when it landed (`clock_timestamp()`, so the steps of one release are not
all stamped identically), how long it took, the build that ran it, and the SHA-256 of the revision
file as it stood at the time.

Two things it answers that the pointer cannot:

- **When an expand/contract rollout's expand landed**, which is what makes "the drain window has
  elapsed" a checkable claim rather than a recollection.
- **Whether a revision's script still says what this database ran.** The chain is only ever tested
  from a clean database, so editing an applied revision leaves `test_migrations_create_schema_without_drift`
  passing while production diverges from the file claiming to describe it.

Before running any step, `alembic upgrade` re-hashes every revision the current heads descend from
and refuses to run if a digest has changed:

```console
FAILED: 1 already-applied revision(s) have been edited since this database ran them: ...
```

The fix is a new revision that applies whatever the edit was going to do. Once you have confirmed
the edits changed no DDL an existing database already ran — a reformat, a corrected docstring —
accept the new digests:

```console
just db-repair-history
```

That appends a `stamp` row per repaired revision rather than rewriting the original, because
nothing in this table is ever edited: the repair is itself part of the history.

Two deliberate silences. A revision with no recorded digest is never reported, because absence
means unknown — a database that predates the ledger, or was stamped past the revision — and not
divergence. And a revision the database has since downgraded past is not checked at all, since it
contributes no DDL to that database and editing it is legitimate.

### Adopting a database older than the ledger

A digest is written when a step runs, so a database that reached its current revision before the
ledger existed has none for the chain behind it, and nothing can ever produce them by running.
Until it adopts, the guard covers only revisions applied from that point on — on an established
database, none of the ones already deployed. Once, after confirming the tree holds the scripts
that database actually ran:

```console
just db-adopt-history
```

This is the same claim `alembic stamp` asks for and carries the same weight. It cannot corrupt the
schema; it declares a set of files to be the truth about one, and every later check is worth
exactly as much as that declaration. New databases need none of it — they record each step as they
run it.

The ledger records only what committed. A failed run rolls its rows back along with the DDL they
describe, which is why there is no `success` column: a step that failed could never have written
one. Failures belong in the migration run's logs. Offline `--sql` mode writes nothing, since there
is no database there to read the ledger's prior state from.
