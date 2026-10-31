"""Add typeahead prefix indexes.

Typeahead prefix-matches facet values and creator names on every keystroke. The existing indexes
serve equality only: under any non-C collation a btree's ordering is not the byte ordering a
`LIKE 'x%'` scan needs, so both queries would fall back to a sequential scan over the whole table.
`text_pattern_ops` gives them an ordering they can use.

Revision ID: 828efb4cf6e0
Revises: a1b2c3d4e5f7
Create Date: 2026-08-15 12:00:00+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "828efb4cf6e0"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FACET_PREFIX_INDEX = """
CREATE INDEX search_document_facets_text_prefix_idx
    ON search_document_facets (field_name, lower(text_value) text_pattern_ops)
    WHERE text_value IS NOT NULL
"""

_CREATOR_PREFIX_INDEX = """
CREATE INDEX creator_aliases_normalized_name_prefix_idx
    ON creator_aliases (normalized_name text_pattern_ops)
"""


def upgrade() -> None:
    """Add the prefix-scan indexes typeahead reads through."""
    op.execute(_FACET_PREFIX_INDEX)
    op.execute(_CREATOR_PREFIX_INDEX)


def downgrade() -> None:
    """Drop the prefix-scan indexes, leaving equality lookups on the existing ones."""
    op.execute("DROP INDEX IF EXISTS creator_aliases_normalized_name_prefix_idx")
    op.execute("DROP INDEX IF EXISTS search_document_facets_text_prefix_idx")
