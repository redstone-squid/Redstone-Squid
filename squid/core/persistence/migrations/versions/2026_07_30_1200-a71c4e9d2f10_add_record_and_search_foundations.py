"""add record and search foundations

Revision ID: a71c4e9d2f10
Revises: 3e191f0adfcf
Create Date: 2026-07-30 12:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a71c4e9d2f10"
down_revision: str | Sequence[str] | None = "3e191f0adfcf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.add_column("builds", sa.Column("completion_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("builds", sa.Column("completion_evidence", sa.Text(), nullable=True))
    op.add_column("builds", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("extenders", sa.Column("orientation", sa.Text(), nullable=True))
    op.add_column("extenders", sa.Column("extension_length", sa.Integer(), nullable=True))
    op.add_column("extenders", sa.Column("extender_type", sa.Text(), nullable=True))
    op.execute(
        """
        UPDATE builds
        SET description = NULLIF(BTRIM(extra_info ->> 'user'), '')
        WHERE extra_info ? 'user'
        """
    )
    op.create_table(
        "record_rulesets",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("document_hash", sa.Text(), nullable=False),
        sa.Column("calculator_version", sa.Text(), nullable=False),
        sa.Column("formatter_version", sa.Text(), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_hash",
            "calculator_version",
            "formatter_version",
            name="record_rulesets_content_key",
        ),
        comment="An immutable version of the record calculators and title formatters.",
    )
    op.create_table(
        "record_definitions",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("ruleset_id", sa.BigInteger(), nullable=False),
        sa.Column("record_class", sa.Text(), nullable=False),
        sa.Column("build_kind", sa.Text(), nullable=False),
        sa.Column("version_scope", sa.Text(), nullable=False),
        sa.Column("version_id", sa.SmallInteger(), nullable=True),
        sa.Column("category_key", sa.Text(), nullable=False),
        sa.Column("materialization_source", sa.Text(), server_default=sa.text("'eager'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "materialization_source IN ('eager', 'seeded', 'public_lookup')",
            name="record_definitions_materialization_source_check",
        ),
        sa.CheckConstraint(
            "record_class IN ('smallest', 'fastest')",
            name="record_definitions_record_class_check",
        ),
        sa.CheckConstraint(
            "version_scope IN ('all_time', 'current')",
            name="record_definitions_version_scope_check",
        ),
        sa.ForeignKeyConstraint(
            ["ruleset_id"],
            ["record_rulesets.id"],
            name="record_definitions_ruleset_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["versions.id"],
            name="record_definitions_version_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "ruleset_id",
            "record_class",
            "build_kind",
            "version_scope",
            "version_id",
            "category_key",
            name="record_definitions_identity_key",
            postgresql_nulls_not_distinct=True,
        ),
        comment="A stable identity for one record competition.",
    )
    op.create_index(
        "record_definitions_category_idx",
        "record_definitions",
        ["build_kind", "record_class", "category_key"],
        unique=False,
    )
    op.create_table(
        "record_definition_facets",
        sa.Column("definition_id", sa.BigInteger(), nullable=False),
        sa.Column("facet_kind", sa.Text(), nullable=False),
        sa.Column("facet_id", sa.Integer(), nullable=False),
        sa.Column("display_order", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.CheckConstraint(
            "facet_kind IN ('restriction', 'type', 'pattern', 'category')",
            name="record_definition_facets_kind_check",
        ),
        sa.ForeignKeyConstraint(
            ["definition_id"],
            ["record_definitions.id"],
            name="record_definition_facets_definition_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("definition_id", "facet_kind", "facet_id"),
        comment="A canonical taxonomy facet belonging to a record definition.",
    )
    op.create_index(
        "record_definition_facets_lookup_idx",
        "record_definition_facets",
        ["facet_kind", "facet_id"],
        unique=False,
    )
    op.create_table(
        "record_computation_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("ruleset_id", sa.BigInteger(), nullable=False),
        sa.Column("build_kind", sa.Text(), nullable=False),
        sa.Column("version_id", sa.SmallInteger(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'running'"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name="record_computation_runs_status_check",
        ),
        sa.ForeignKeyConstraint(
            ["ruleset_id"],
            ["record_rulesets.id"],
            name="record_computation_runs_ruleset_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["versions.id"],
            name="record_computation_runs_version_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        comment="An immutable attempt to calculate records for one build and version scope.",
    )
    op.create_index(
        "record_computation_runs_one_active_idx",
        "record_computation_runs",
        ["ruleset_id", "build_kind", "version_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
        postgresql_nulls_not_distinct=True,
    )
    op.create_index(
        "record_computation_runs_started_idx",
        "record_computation_runs",
        ["started_at"],
        unique=False,
    )
    op.create_table(
        "record_results",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("definition_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "gap_reasons",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("provisional_build_id", sa.BigInteger(), nullable=True),
        sa.Column("history_complete", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('resolved', 'unresolved', 'no_candidate')",
            name="record_results_status_check",
        ),
        sa.ForeignKeyConstraint(
            ["definition_id"],
            ["record_definitions.id"],
            name="record_results_definition_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["provisional_build_id"],
            ["builds.id"],
            name="record_results_provisional_build_id_fkey",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["record_computation_runs.id"],
            name="record_results_run_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "definition_id", name="record_results_run_definition_key"),
        comment="The outcome for one definition in a computation run.",
    )
    op.create_index("record_results_definition_idx", "record_results", ["definition_id"], unique=False)
    op.create_table(
        "record_result_holders",
        sa.Column("result_id", sa.BigInteger(), nullable=False),
        sa.Column("build_id", sa.BigInteger(), nullable=False),
        sa.Column("rank", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("metric_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("subtitle", sa.Text(), nullable=True),
        sa.Column("completion_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("rank > 0", name="record_result_holders_rank_check"),
        sa.ForeignKeyConstraint(
            ["build_id"],
            ["builds.id"],
            name="record_result_holders_build_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["result_id"],
            ["record_results.id"],
            name="record_result_holders_result_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("result_id", "build_id"),
        comment="A co-holder of a resolved computed record.",
    )
    op.create_index("record_result_holders_build_idx", "record_result_holders", ["build_id"], unique=False)
    op.create_table(
        "record_holder_history",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("definition_id", sa.BigInteger(), nullable=False),
        sa.Column("build_id", sa.BigInteger(), nullable=False),
        sa.Column("predecessor_id", sa.BigInteger(), nullable=True),
        sa.Column("held_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("held_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metric_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            "held_until IS NULL OR held_until >= held_from",
            name="record_holder_history_interval_check",
        ),
        sa.ForeignKeyConstraint(
            ["build_id"],
            ["builds.id"],
            name="record_holder_history_build_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["definition_id"],
            ["record_definitions.id"],
            name="record_holder_history_definition_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["predecessor_id"],
            ["record_holder_history.id"],
            name="record_holder_history_predecessor_id_fkey",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["record_computation_runs.id"],
            name="record_holder_history_run_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "definition_id",
            "build_id",
            "held_from",
            name="record_holder_history_identity_key",
        ),
        comment="A reconstructed interval in a definition's beaten-record chronology.",
    )
    op.create_index(
        "record_holder_history_definition_idx",
        "record_holder_history",
        ["definition_id", "held_from"],
        unique=False,
    )
    op.create_table(
        "record_recompute_queue",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("scope_key", sa.Text(), nullable=False),
        sa.Column("build_kind", sa.Text(), nullable=False),
        sa.Column("build_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "reasons", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        sa.Column("enqueued_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["build_id"],
            ["builds.id"],
            name="record_recompute_queue_build_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scope_key", name="record_recompute_queue_scope_key_key"),
        comment="A durable request to recompute an affected record scope.",
    )
    op.create_index(
        "record_recompute_queue_ready_idx",
        "record_recompute_queue",
        ["enqueued_at"],
        unique=False,
        postgresql_where=sa.text("locked_at IS NULL"),
    )
    op.create_table(
        "door_timing_variants",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("build_id", sa.BigInteger(), nullable=False),
        sa.Column("label", sa.Text(), server_default=sa.text("'default'"), nullable=False),
        sa.Column("opening_time", sa.BigInteger(), nullable=True),
        sa.Column("visible_opening_time", sa.BigInteger(), nullable=True),
        sa.Column("closing_time", sa.BigInteger(), nullable=True),
        sa.Column("visible_closing_time", sa.BigInteger(), nullable=True),
        sa.Column("opening_reset_time", sa.BigInteger(), nullable=True),
        sa.Column("closing_reset_time", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["build_id"],
            ["doors.build_id"],
            name="door_timing_variants_build_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("build_id", "label", name="door_timing_variants_build_label_key"),
        comment="A measured door timing variant used for lexicographic fastest records.",
    )
    op.create_table(
        "extender_timing_variants",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("build_id", sa.BigInteger(), nullable=False),
        sa.Column("label", sa.Text(), server_default=sa.text("'default'"), nullable=False),
        sa.Column("retraction_time", sa.BigInteger(), nullable=True),
        sa.Column("extension_time", sa.BigInteger(), nullable=True),
        sa.Column("retraction_reset_time", sa.BigInteger(), nullable=True),
        sa.Column("extension_reset_time", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["build_id"],
            ["extenders.build_id"],
            name="extender_timing_variants_build_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("build_id", "label", name="extender_timing_variants_build_label_key"),
        comment="A measured piston-extender timing variant used for fastest records.",
    )
    op.execute(
        """
        INSERT INTO door_timing_variants (
            build_id,
            label,
            opening_time,
            visible_opening_time,
            closing_time,
            visible_closing_time
        )
        SELECT
            build_id,
            'legacy',
            normal_opening_time,
            visible_opening_time,
            normal_closing_time,
            visible_closing_time
        FROM doors
        WHERE normal_opening_time IS NOT NULL
           OR visible_opening_time IS NOT NULL
           OR normal_closing_time IS NOT NULL
           OR visible_closing_time IS NOT NULL
        """
    )
    op.create_table(
        "search_documents",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("resource_kind", sa.Text(), nullable=False),
        sa.Column("source_key", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("subtitle", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("normalized_title", sa.Text(), nullable=False),
        sa.Column("fuzzy_text", sa.Text(), nullable=False),
        sa.Column("tags", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False),
        sa.Column("title_vector", postgresql.TSVECTOR(), nullable=True),
        sa.Column("description_vector", postgresql.TSVECTOR(), nullable=True),
        sa.Column("combined_vector", postgresql.TSVECTOR(), nullable=True),
        sa.Column(
            "document_data",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("source_hash", sa.Text(), nullable=False),
        sa.Column("embedding", VECTOR(dim=1536), nullable=True),
        sa.Column("embedding_model", sa.Text(), nullable=True),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("resource_kind", "source_key", name="search_documents_resource_key"),
        comment="An indexed projection of a searchable application resource.",
    )
    op.create_index(
        "search_documents_combined_fts_idx",
        "search_documents",
        ["combined_vector"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        "search_documents_description_fts_idx",
        "search_documents",
        ["description_vector"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        "search_documents_fuzzy_trgm_idx",
        "search_documents",
        ["fuzzy_text"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"fuzzy_text": "gin_trgm_ops"},
    )
    op.create_index(
        "search_documents_scope_idx",
        "search_documents",
        ["resource_kind", "status"],
        unique=False,
    )
    op.create_index(
        "search_documents_tags_idx",
        "search_documents",
        ["tags"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        "search_documents_title_fts_idx",
        "search_documents",
        ["title_vector"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_table(
        "search_document_facets",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("field_name", sa.Text(), nullable=False),
        sa.Column("ordinal", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("text_value", sa.Text(), nullable=True),
        sa.Column("numeric_value", sa.Numeric(), nullable=True),
        sa.Column("timestamp_value", sa.DateTime(timezone=True), nullable=True),
        sa.Column("boolean_value", sa.Boolean(), nullable=True),
        sa.CheckConstraint(
            "num_nonnulls(text_value, numeric_value, timestamp_value, boolean_value) = 1",
            name="search_document_facets_one_value_check",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["search_documents.id"],
            name="search_document_facets_document_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id",
            "field_name",
            "ordinal",
            name="search_document_facets_identity_key",
        ),
        comment="A typed, indexed field value belonging to a search document.",
    )
    op.create_index(
        "search_document_facets_boolean_idx",
        "search_document_facets",
        ["field_name", "boolean_value"],
        unique=False,
        postgresql_where=sa.text("boolean_value IS NOT NULL"),
    )
    op.create_index(
        "search_document_facets_numeric_idx",
        "search_document_facets",
        ["field_name", "numeric_value"],
        unique=False,
        postgresql_where=sa.text("numeric_value IS NOT NULL"),
    )
    op.create_index(
        "search_document_facets_text_idx",
        "search_document_facets",
        ["field_name", "text_value"],
        unique=False,
        postgresql_where=sa.text("text_value IS NOT NULL"),
    )
    op.create_index(
        "search_document_facets_timestamp_idx",
        "search_document_facets",
        ["field_name", "timestamp_value"],
        unique=False,
        postgresql_where=sa.text("timestamp_value IS NOT NULL"),
    )
    op.create_table(
        "search_projection_queue",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("resource_kind", sa.Text(), nullable=False),
        sa.Column("source_key", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), server_default=sa.text("'upsert'"), nullable=False),
        sa.Column("enqueued_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "action IN ('upsert', 'delete')",
            name="search_projection_queue_action_check",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "resource_kind",
            "source_key",
            name="search_projection_queue_resource_key",
        ),
        comment="A durable request to refresh or delete a projected search resource.",
    )
    op.create_index(
        "search_projection_queue_ready_idx",
        "search_projection_queue",
        ["enqueued_at"],
        unique=False,
        postgresql_where=sa.text("locked_at IS NULL"),
    )
    op.create_table(
        "search_embedding_queue",
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("source_hash", sa.Text(), nullable=False),
        sa.Column("enqueued_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["search_documents.id"],
            name="search_embedding_queue_document_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("document_id"),
        comment="A durable request to embed a search document whose source hash changed.",
    )
    op.create_index(
        "search_embedding_queue_ready_idx",
        "search_embedding_queue",
        ["enqueued_at"],
        unique=False,
        postgresql_where=sa.text("locked_at IS NULL"),
    )


def downgrade() -> None:
    """Revert this revision."""
    op.drop_index("search_embedding_queue_ready_idx", table_name="search_embedding_queue")
    op.drop_table("search_embedding_queue")
    op.drop_index("search_projection_queue_ready_idx", table_name="search_projection_queue")
    op.drop_table("search_projection_queue")
    op.drop_index("search_document_facets_timestamp_idx", table_name="search_document_facets")
    op.drop_index("search_document_facets_text_idx", table_name="search_document_facets")
    op.drop_index("search_document_facets_numeric_idx", table_name="search_document_facets")
    op.drop_index("search_document_facets_boolean_idx", table_name="search_document_facets")
    op.drop_table("search_document_facets")
    op.drop_index("search_documents_title_fts_idx", table_name="search_documents", postgresql_using="gin")
    op.drop_index("search_documents_tags_idx", table_name="search_documents", postgresql_using="gin")
    op.drop_index("search_documents_scope_idx", table_name="search_documents")
    op.drop_index("search_documents_fuzzy_trgm_idx", table_name="search_documents", postgresql_using="gin")
    op.drop_index("search_documents_description_fts_idx", table_name="search_documents", postgresql_using="gin")
    op.drop_index("search_documents_combined_fts_idx", table_name="search_documents", postgresql_using="gin")
    op.drop_table("search_documents")
    op.drop_table("extender_timing_variants")
    op.drop_table("door_timing_variants")
    op.drop_index("record_recompute_queue_ready_idx", table_name="record_recompute_queue")
    op.drop_table("record_recompute_queue")
    op.drop_index("record_holder_history_definition_idx", table_name="record_holder_history")
    op.drop_table("record_holder_history")
    op.drop_index("record_result_holders_build_idx", table_name="record_result_holders")
    op.drop_table("record_result_holders")
    op.drop_index("record_results_definition_idx", table_name="record_results")
    op.drop_table("record_results")
    op.drop_index("record_computation_runs_started_idx", table_name="record_computation_runs")
    op.drop_index("record_computation_runs_one_active_idx", table_name="record_computation_runs")
    op.drop_table("record_computation_runs")
    op.drop_index("record_definition_facets_lookup_idx", table_name="record_definition_facets")
    op.drop_table("record_definition_facets")
    op.drop_index("record_definitions_category_idx", table_name="record_definitions")
    op.drop_table("record_definitions")
    op.drop_table("record_rulesets")
    op.drop_column("extenders", "extender_type")
    op.drop_column("extenders", "extension_length")
    op.drop_column("extenders", "orientation")
    op.drop_column("builds", "description")
    op.drop_column("builds", "completion_evidence")
    op.drop_column("builds", "completion_at")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
    op.execute("DROP EXTENSION IF EXISTS unaccent")
