"""add unified tag taxonomy

Revision ID: 51f9d6b2a8c4
Revises: c9b2d861f540
Create Date: 2026-07-30 16:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "51f9d6b2a8c4"
down_revision: str | Sequence[str] | None = "c9b2d861f540"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create typed tag definitions and build assignments."""
    op.create_table(
        "tag_units",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("dimension", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("aliases", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("scale_to_base", sa.Numeric(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
        comment="A unit accepted by numeric tag inputs.",
    )
    op.create_table(
        "tag_definitions",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("stable_key", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("normalized_name", sa.Text(), nullable=False),
        sa.Column("query_name", sa.Text(), nullable=True),
        sa.Column("authority", sa.Text(), nullable=False),
        sa.Column("semantic_kind", sa.Text(), nullable=False),
        sa.Column("restriction_type", sa.Text(), nullable=True),
        sa.Column("value_type", sa.Text(), nullable=False),
        sa.Column("record_operator", sa.Text(), nullable=True),
        sa.Column("canonical_unit_key", sa.Text(), nullable=True),
        sa.Column("default_display_unit_key", sa.Text(), nullable=True),
        sa.Column("numeric_quantum", sa.Numeric(), nullable=True),
        sa.Column("render_template", sa.Text(), nullable=False),
        sa.Column("default_display_order", sa.SmallInteger(), nullable=False),
        sa.Column("moderation_status", sa.Text(), nullable=False),
        sa.Column("created_by_discord_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("authority IN ('official', 'user')", name="tag_definitions_authority_check"),
        sa.CheckConstraint(
            "semantic_kind IN ('restriction', 'pattern', 'showcase')",
            name="tag_definitions_semantic_kind_check",
        ),
        sa.CheckConstraint(
            "value_type IN ('none', 'numeric', 'text', 'boolean')",
            name="tag_definitions_value_type_check",
        ),
        sa.CheckConstraint(
            "moderation_status IN ('pending', 'approved', 'rejected', 'archived')",
            name="tag_definitions_moderation_status_check",
        ),
        sa.CheckConstraint(
            "record_operator IS NULL OR record_operator IN ('present', 'exact', 'at_most', 'at_least')",
            name="tag_definitions_record_operator_check",
        ),
        sa.CheckConstraint(
            "query_name IS NULL OR query_name ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="tag_definitions_query_name_format_check",
        ),
        sa.CheckConstraint(
            "(authority = 'official') OR "
            "(semantic_kind = 'showcase' AND restriction_type IS NULL AND record_operator IS NULL)",
            name="tag_definitions_user_showcase_only_check",
        ),
        sa.CheckConstraint(
            "(semantic_kind = 'restriction' AND restriction_type IS NOT NULL) OR "
            "(semantic_kind <> 'restriction' AND restriction_type IS NULL)",
            name="tag_definitions_restriction_type_check",
        ),
        sa.CheckConstraint(
            "(value_type = 'numeric') = "
            "(canonical_unit_key IS NOT NULL OR numeric_quantum IS NOT NULL) OR "
            "(value_type = 'numeric' AND canonical_unit_key IS NULL AND numeric_quantum IS NULL)",
            name="tag_definitions_numeric_metadata_check",
        ),
        sa.CheckConstraint(
            "value_type = 'numeric' OR "
            "(canonical_unit_key IS NULL AND default_display_unit_key IS NULL AND numeric_quantum IS NULL)",
            name="tag_definitions_non_numeric_unit_check",
        ),
        sa.CheckConstraint(
            "numeric_quantum IS NULL OR numeric_quantum > 0",
            name="tag_definitions_numeric_quantum_check",
        ),
        sa.CheckConstraint(
            "(record_operator = 'present' AND value_type = 'none') OR "
            "(record_operator IN ('at_most', 'at_least') AND value_type = 'numeric') OR "
            "(record_operator = 'exact' AND value_type <> 'none') OR record_operator IS NULL",
            name="tag_definitions_record_operator_value_check",
        ),
        sa.CheckConstraint("default_display_order >= 0", name="tag_definitions_display_order_check"),
        sa.ForeignKeyConstraint(
            ["canonical_unit_key"],
            ["tag_units.key"],
            name="tag_definitions_canonical_unit_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["default_display_unit_key"],
            ["tag_units.key"],
            name="tag_definitions_display_unit_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "value_type", name="tag_definitions_id_value_type_key"),
        sa.UniqueConstraint("query_name", name="tag_definitions_query_name_key"),
        sa.UniqueConstraint("stable_key", name="tag_definitions_stable_key_key"),
        comment="A canonical tag that may be assigned to builds.",
    )
    op.create_index(
        "tag_definitions_lookup_idx",
        "tag_definitions",
        ["normalized_name", "semantic_kind"],
        unique=False,
    )
    op.create_table(
        "tag_aliases",
        sa.Column("tag_id", sa.BigInteger(), nullable=False),
        sa.Column("alias", sa.Text(), nullable=False),
        sa.Column("normalized_alias", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["tag_id"],
            ["tag_definitions.id"],
            name="tag_aliases_tag_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tag_id", "normalized_alias"),
        comment="An alternate display name for a tag.",
    )
    op.create_index("tag_aliases_normalized_idx", "tag_aliases", ["normalized_alias"], unique=False)
    op.create_table(
        "tag_applicabilities",
        sa.Column("tag_id", sa.BigInteger(), nullable=False),
        sa.Column("build_kind", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tag_id"],
            ["tag_definitions.id"],
            name="tag_applicabilities_tag_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tag_id", "build_kind"),
        comment="A build kind on which a tag may be used.",
    )
    op.create_table(
        "build_tag_assignments",
        sa.Column("build_id", sa.BigInteger(), nullable=False),
        sa.Column("tag_id", sa.BigInteger(), nullable=False),
        sa.Column("value_type", sa.Text(), nullable=False),
        sa.Column("numeric_value", sa.Numeric(), nullable=True),
        sa.Column("text_value", sa.Text(), nullable=True),
        sa.Column("boolean_value", sa.Boolean(), nullable=True),
        sa.Column("display_unit_key", sa.Text(), nullable=True),
        sa.Column("display_order", sa.SmallInteger(), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("provenance", sa.Text(), nullable=False),
        sa.Column("created_by_discord_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "(value_type = 'none' AND num_nonnulls(numeric_value, text_value, boolean_value) = 0) OR "
            "(value_type = 'numeric' AND numeric_value IS NOT NULL "
            "AND num_nonnulls(text_value, boolean_value) = 0) OR "
            "(value_type = 'text' AND text_value IS NOT NULL "
            "AND num_nonnulls(numeric_value, boolean_value) = 0) OR "
            "(value_type = 'boolean' AND boolean_value IS NOT NULL "
            "AND num_nonnulls(numeric_value, text_value) = 0)",
            name="build_tag_assignments_typed_value_check",
        ),
        sa.CheckConstraint(
            "numeric_value IS NULL OR numeric_value::text NOT IN ('NaN', 'Infinity', '-Infinity')",
            name="build_tag_assignments_finite_numeric_check",
        ),
        sa.CheckConstraint(
            "display_order IS NULL OR display_order >= 0",
            name="build_tag_assignments_order_check",
        ),
        sa.CheckConstraint(
            "provenance IN ('submitted', 'inferred', 'moderated', 'legacy_import')",
            name="build_tag_assignments_provenance_check",
        ),
        sa.ForeignKeyConstraint(
            ["build_id"],
            ["builds.id"],
            name="build_tag_assignments_build_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["display_unit_key"],
            ["tag_units.key"],
            name="build_tag_assignments_display_unit_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tag_id", "value_type"],
            ["tag_definitions.id", "tag_definitions.value_type"],
            name="build_tag_assignments_definition_value_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("build_id", "tag_id"),
        comment="A typed tag value attached to one build.",
    )
    op.create_index(
        "build_tag_assignments_tag_build_idx",
        "build_tag_assignments",
        ["tag_id", "build_id"],
        unique=False,
    )
    op.create_index(
        "build_tag_assignments_numeric_idx",
        "build_tag_assignments",
        ["tag_id", "numeric_value", "build_id"],
        unique=False,
        postgresql_where=sa.text("numeric_value IS NOT NULL"),
    )
    op.create_index(
        "build_tag_assignments_text_idx",
        "build_tag_assignments",
        ["tag_id", "text_value", "build_id"],
        unique=False,
        postgresql_where=sa.text("text_value IS NOT NULL"),
    )
    op.create_table(
        "tag_relations",
        sa.Column("source_tag_id", sa.BigInteger(), nullable=False),
        sa.Column("relation_kind", sa.Text(), nullable=False),
        sa.Column("target_tag_id", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "relation_kind IN ('implies', 'incompatible')",
            name="tag_relations_kind_check",
        ),
        sa.CheckConstraint("source_tag_id <> target_tag_id", name="tag_relations_distinct_check"),
        sa.ForeignKeyConstraint(
            ["source_tag_id"],
            ["tag_definitions.id"],
            name="tag_relations_source_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_tag_id"],
            ["tag_definitions.id"],
            name="tag_relations_target_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("source_tag_id", "relation_kind", "target_tag_id"),
        comment="A semantic relationship between official restrictions.",
    )
    op.create_table(
        "tag_record_thresholds",
        sa.Column("tag_id", sa.BigInteger(), nullable=False),
        sa.Column("value_type", sa.Text(), nullable=False),
        sa.Column("numeric_value", sa.Numeric(), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.CheckConstraint("value_type = 'numeric'", name="tag_record_thresholds_numeric_check"),
        sa.ForeignKeyConstraint(
            ["tag_id", "value_type"],
            ["tag_definitions.id", "tag_definitions.value_type"],
            name="tag_record_thresholds_definition_value_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tag_id", "numeric_value"),
        comment="A staff-seeded eager threshold for a parameterized restriction.",
    )
    op.bulk_insert(
        sa.table(
            "tag_units",
            sa.column("key", sa.Text()),
            sa.column("dimension", sa.Text()),
            sa.column("symbol", sa.Text()),
            sa.column("aliases", postgresql.ARRAY(sa.Text())),
            sa.column("scale_to_base", sa.Numeric()),
        ),
        [
            {
                "key": "block",
                "dimension": "length",
                "symbol": "blocks",
                "aliases": ["block", "blocks", "b"],
                "scale_to_base": 1,
            },
            {
                "key": "game_tick",
                "dimension": "time",
                "symbol": "gt",
                "aliases": ["gt", "gametick", "gameticks"],
                "scale_to_base": 1,
            },
            {
                "key": "second",
                "dimension": "time",
                "symbol": "s",
                "aliases": ["s", "sec", "second", "seconds"],
                "scale_to_base": 20,
            },
        ],
    )


def downgrade() -> None:
    """Remove unified tag storage."""
    op.drop_table("tag_record_thresholds")
    op.drop_table("tag_relations")
    op.drop_index("build_tag_assignments_text_idx", table_name="build_tag_assignments")
    op.drop_index("build_tag_assignments_numeric_idx", table_name="build_tag_assignments")
    op.drop_index("build_tag_assignments_tag_build_idx", table_name="build_tag_assignments")
    op.drop_table("build_tag_assignments")
    op.drop_table("tag_applicabilities")
    op.drop_index("tag_aliases_normalized_idx", table_name="tag_aliases")
    op.drop_table("tag_aliases")
    op.drop_index("tag_definitions_lookup_idx", table_name="tag_definitions")
    op.drop_table("tag_definitions")
    op.drop_table("tag_units")
