"""Fold creator names in the application rather than in Postgres.

`creator_aliases.normalized_name` was `GENERATED ALWAYS AS (lower(btrim(name)))`, while six call
sites built the same comparison value in Python as `name.strip().lower()`. The two do not agree, and
cannot be made to: Postgres `lower()` follows the database's glibc collation (`datcollversion`) and
`btrim()` strips only U+0020. Under `en_US.utf8` the SQL side folds `ΣΣ` to `σσ` where Python gives
`σς`, and `İ` to `i` where Python gives `i̇`. Crediting such a name on a second build missed the
lookup, conflicted on insert, and raised NoResultFound from the fallback re-select.

The two foldings also disagree about *what collides*, in both directions -- `Straße`/`Strasse`
collide under Python's casefold but not under SQL `lower`, `I`/`İ` the reverse -- so keeping both
would mean two conflicting notions of creator identity rather than one authority. The application
wins: `fold_creator_name` is `NFKC -> strip -> casefold`, which is the Unicode operation for caseless
matching, is pinned to CPython's Unicode version rather than the host's libc, and has equivalents in
every language.

The column therefore becomes a plain one written by a SQLAlchemy column default derived from `name`,
so no insert path can skip it. A check constraint catches a raw SQL write that stored the display
spelling verbatim; it asserts only what holds for any casefold output, so it cannot reject a
legitimately folded name.

Revision ID: e5f6a7b8c9d2
Revises: b1c2d3e4f5a7
Create Date: 2026-08-16 10:00:00+00:00
"""

# ruff: noqa: RUF002  Confusable and compatibility characters are the subject
# matter here: they are the inputs whose folding this file exists to pin.

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from squid.accounts.domain import fold_creator_name

revision: str = "e5f6a7b8c9d2"
down_revision: str | Sequence[str] | None = "b1c2d3e4f5a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FOLDED_CHECK = "normalized_name = btrim(normalized_name) AND normalized_name !~ '[A-Z]'"
_PREFIX_INDEX = """
CREATE INDEX creator_aliases_normalized_name_prefix_idx
    ON creator_aliases (normalized_name text_pattern_ops)
"""


def _refold(connection: sa.Connection) -> None:
    """Rewrite every stored fold using the application's function.

    Postgres cannot compute this, so the rows have to round-trip through Python. Creator
    aliases are a small table and this runs once, so a single pass is fine.
    """
    rows = connection.execute(sa.text("SELECT id, name FROM creator_aliases ORDER BY id")).all()
    if not rows:
        return
    connection.execute(
        sa.text("UPDATE creator_aliases SET normalized_name = :folded WHERE id = :id"),
        [{"id": row.id, "folded": fold_creator_name(row.name)} for row in rows],
    )


def upgrade() -> None:
    """Hand ownership of the fold to the application."""
    connection = op.get_bind()
    # Dropping the column would take the unique constraint and prefix index with it, so
    # degenerate it in place instead: a generated column cannot be written, but it can stop
    # being generated while keeping its data, name, and every index built on it.
    op.execute("ALTER TABLE creator_aliases ALTER COLUMN normalized_name DROP EXPRESSION")
    _refold(connection)
    op.create_check_constraint("creator_aliases_normalized_name_folded", "creator_aliases", _FOLDED_CHECK)


def downgrade() -> None:
    """Return the fold to Postgres, losing the distinctions only casefold makes.

    Names that differ under `lower(btrim(...))` but collide under casefold cannot both survive
    the regenerated unique index. There is no correct automatic answer, so the rebuild is left
    to fail loudly rather than silently discarding a creator credit.
    """
    op.drop_constraint("creator_aliases_normalized_name_folded", "creator_aliases", type_="check")
    op.drop_index("creator_aliases_normalized_name_prefix_idx", table_name="creator_aliases")
    op.drop_constraint("creator_aliases_normalized_name_key", "creator_aliases", type_="unique")
    op.drop_column("creator_aliases", "normalized_name")
    op.add_column(
        "creator_aliases",
        sa.Column("normalized_name", sa.Text(), sa.Computed("lower(btrim(name))", persisted=True), nullable=False),
    )
    op.create_unique_constraint("creator_aliases_normalized_name_key", "creator_aliases", ["normalized_name"])
    op.execute(_PREFIX_INDEX)
