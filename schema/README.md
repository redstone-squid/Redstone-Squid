`structure/` is the database schema split into one file per table and function, for browsing and for
readable diffs. It is generated, so do not edit it. CI rebuilds it from the Alembic migrations whenever they
change, and `just schema-docs` does the same locally (needs docker). `../schema_dump.sql` is the same schema as
a single file.

Each table's file has its columns, constraints (including foreign keys), indexes, triggers and comments.
`structure/index.sql` lists the files, but they cannot be loaded in that order because foreign keys sit in the
tables that have them; use `schema_dump.sql` or the migrations to build a database. The bot does not read
this directory.

The files are produced by our fork of [pg_split_dump](https://github.com/Glinte/pg_split_dump); see
`scripts/export_schema.sh`.
