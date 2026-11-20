"""cut over legacy taxonomy

Revision ID: 8d7c2e4a91b6
Revises: 51f9d6b2a8c4
Create Date: 2026-07-30 17:00:00+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "8d7c2e4a91b6"
down_revision: str | Sequence[str] | None = "51f9d6b2a8c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Import official categories once and discard all rebuildable projections."""
    op.execute(
        """
        INSERT INTO tag_definitions (
            stable_key, display_name, normalized_name, authority, semantic_kind,
            restriction_type, value_type, record_operator, render_template,
            default_display_order, moderation_status
        )
        SELECT
            CASE WHEN lower(trim(name)) = 'expandable' THEN 'expandable'
                 ELSE 'legacy_restriction_' || id::text END,
            name, lower(regexp_replace(trim(name), '\\s+', ' ', 'g')),
            'official', 'restriction', COALESCE(type, 'miscellaneous'),
            'none', 'present', '{name}', id, 'approved'
        FROM restrictions
        WHERE name IS NOT NULL
        ON CONFLICT (stable_key) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO tag_definitions (
            stable_key, display_name, normalized_name, authority, semantic_kind,
            value_type, render_template, default_display_order, moderation_status
        )
        SELECT
            'legacy_pattern_' || id::text, name,
            lower(regexp_replace(trim(name), '\\s+', ' ', 'g')),
            'official', 'pattern', 'none', '{name}', id, 'approved'
        FROM types
        WHERE name IS NOT NULL
        ON CONFLICT (stable_key) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO tag_aliases (tag_id, alias, normalized_alias, created_at)
        SELECT d.id, a.alias, lower(regexp_replace(trim(a.alias), '\\s+', ' ', 'g')), a.created_at
        FROM restriction_aliases a
        JOIN restrictions r ON r.id = a.restriction_id
        JOIN tag_definitions d ON
            d.stable_key = CASE WHEN lower(trim(r.name)) = 'expandable' THEN 'expandable'
                                ELSE 'legacy_restriction_' || r.id::text END
        ON CONFLICT DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO tag_applicabilities (tag_id, build_kind)
        SELECT d.id, r.build_category
        FROM restrictions r
        JOIN tag_definitions d ON
            d.stable_key = CASE WHEN lower(trim(r.name)) = 'expandable' THEN 'expandable'
                                ELSE 'legacy_restriction_' || r.id::text END
        WHERE r.build_category IS NOT NULL
        ON CONFLICT DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO tag_applicabilities (tag_id, build_kind)
        SELECT d.id, t.build_category
        FROM types t
        JOIN tag_definitions d ON d.stable_key = 'legacy_pattern_' || t.id::text
        WHERE t.build_category IS NOT NULL
        ON CONFLICT DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO build_tag_assignments (
            build_id, tag_id, value_type, provenance, created_at, updated_at
        )
        SELECT br.build_id, d.id, 'none', 'legacy_import', now(), now()
        FROM build_restrictions br
        JOIN restrictions r ON r.id = br.restriction_id
        JOIN tag_definitions d ON
            d.stable_key = CASE WHEN lower(trim(r.name)) = 'expandable' THEN 'expandable'
                                ELSE 'legacy_restriction_' || r.id::text END
        ON CONFLICT (build_id, tag_id) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO build_tag_assignments (
            build_id, tag_id, value_type, provenance, created_at, updated_at
        )
        SELECT bt.build_id, d.id, 'none', 'legacy_import', now(), now()
        FROM build_types bt
        JOIN tag_definitions d ON d.stable_key = 'legacy_pattern_' || bt.type_id::text
        ON CONFLICT (build_id, tag_id) DO NOTHING
        """
    )
    op.execute(
        """
        CREATE TRIGGER build_tag_assignments_enqueue_search
        AFTER INSERT OR DELETE OR UPDATE ON build_tag_assignments
        FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection()
        """
    )
    _discard_and_enqueue()


def downgrade() -> None:
    """Remove imported rows; projections remain intentionally rebuildable."""
    op.execute("DROP TRIGGER IF EXISTS build_tag_assignments_enqueue_search ON build_tag_assignments")
    op.execute(
        """
        DELETE FROM tag_definitions
        WHERE stable_key = 'expandable'
           OR stable_key LIKE 'legacy_restriction_%'
           OR stable_key LIKE 'legacy_pattern_%'
        """
    )
    _discard_and_enqueue()


def _discard_and_enqueue() -> None:
    op.execute("TRUNCATE TABLE search_projection_queue, search_documents RESTART IDENTITY CASCADE")
    op.execute(
        """
        TRUNCATE TABLE
            record_recompute_queue, record_holder_history, record_result_holders,
            record_results, record_computation_runs, record_definition_facets,
            record_definitions
        RESTART IDENTITY CASCADE
        """
    )
    op.execute(
        """
        INSERT INTO search_projection_queue (resource_kind, source_key, action)
        SELECT 'build', id::text, 'upsert' FROM builds
        """
    )
    op.execute(
        """
        INSERT INTO search_projection_queue (resource_kind, source_key, action)
        SELECT 'metadata', 'tag:' || id::text, 'upsert'
        FROM tag_definitions
        WHERE moderation_status = 'approved'
        """
    )
    op.execute(
        """
        INSERT INTO record_recompute_queue (scope_key, build_kind, build_id, reasons)
        SELECT 'build:' || id::text, category, id, '["taxonomy_cutover"]'::jsonb
        FROM builds
        WHERE category IS NOT NULL
        """
    )
