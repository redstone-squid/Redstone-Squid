This directory holds two generated views of the database schema and one hand-written seed file. The bot
does not read any of it.

`structure/` is the schema split into one file per table and function, for browsing and for readable diffs.
`schema_dump.sql` is the same schema as a single file, for grep and for pasting into a review. Both are
generated, so do not edit them. CI rebuilds them from the Alembic migrations whenever they change, and
`just schema-docs` does the same locally (needs docker).

Each table's file has its columns, constraints (including foreign keys), indexes, triggers and comments.
`structure/index.sql` lists the files, but they cannot be loaded in that order because foreign keys sit in the
tables that have them; use `schema_dump.sql` or the migrations to build a database.

The split files are produced by our fork of [pg_split_dump](https://github.com/Glinte/pg_split_dump); see
`scripts/export_schema.sh`.

`seed.sql` is written by hand. It inserts example server settings, the official door tags, and a fixed
Minecraft version list into a migrated database. The inserts are not upserts, so run it once against empty
tables.
