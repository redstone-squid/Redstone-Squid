"""Finish the unified taxonomy cutover.

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-08-10 17:00:00+00:00
"""

from collections.abc import Sequence
from typing import TypeVar

import sqlalchemy as sa
from alembic_utils.pg_function import PGFunction
from alembic_utils.pg_trigger import PGTrigger
from alembic_utils.replaceable_entity import ReplaceableEntity

from alembic import op
from squid.persistence.alembic_entities import alembic_util_entities

revision: str = "c9d0e1f2a3b4"
down_revision: str | Sequence[str] | None = "b8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEGACY_TABLE_TRIGGERS = (
    ("trg_sync_on_tag", "restrictions"),
    ("trg_sync_on_tag_alias", "restriction_aliases"),
    ("build_restrictions_enqueue_search", "build_restrictions"),
    ("build_types_enqueue_search", "build_types"),
    ("restrictions_enqueue_search", "restrictions"),
    ("restriction_aliases_enqueue_search", "restriction_aliases"),
    ("types_enqueue_search", "types"),
    ("build_restrictions_enqueue_discord_sync", "build_restrictions"),
    ("build_types_enqueue_discord_sync", "build_types"),
)
_TAG_TRIGGER_NAMES = {"tag_definitions_enqueue_search", "tag_aliases_enqueue_search"}
_LEGACY_METADATA_FUNCTION = """
CREATE OR REPLACE FUNCTION public.enqueue_metadata_search_projection() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    target_id bigint;
    target_kind text;
    target_action text := 'upsert';
BEGIN
    target_kind := CASE TG_TABLE_NAME
        WHEN 'restrictions' THEN 'restriction'
        WHEN 'restriction_aliases' THEN 'restriction'
        WHEN 'types' THEN 'type'
        WHEN 'creator_aliases' THEN 'creator'
        WHEN 'versions' THEN 'version'
    END;
    IF TG_TABLE_NAME = 'restriction_aliases' THEN
        target_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.restriction_id ELSE NEW.restriction_id END;
    ELSE
        target_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;
    END IF;
    IF TG_OP = 'DELETE' AND TG_TABLE_NAME <> 'restriction_aliases' THEN
        target_action := 'delete';
    END IF;
    INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
    VALUES ('metadata', target_kind || ':' || target_id::text, target_action, now())
    ON CONFLICT (resource_kind, source_key) DO UPDATE
    SET action = EXCLUDED.action, enqueued_at = EXCLUDED.enqueued_at,
        attempts = 0, locked_at = NULL, last_error = NULL;
    IF TG_TABLE_NAME IN ('restrictions', 'restriction_aliases') THEN
        INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
        SELECT 'build', br.build_id::text, 'upsert', now()
        FROM public.build_restrictions br WHERE br.restriction_id = target_id
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = 'upsert', enqueued_at = EXCLUDED.enqueued_at, locked_at = NULL;
    ELSIF TG_TABLE_NAME = 'types' THEN
        INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
        SELECT 'build', bt.build_id::text, 'upsert', now()
        FROM public.build_types bt WHERE bt.type_id = target_id
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = 'upsert', enqueued_at = EXCLUDED.enqueued_at, locked_at = NULL;
    ELSIF TG_TABLE_NAME = 'creator_aliases' THEN
        INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
        SELECT 'build', bc.build_id::text, 'upsert', now()
        FROM public.build_creators bc WHERE bc.alias_id = target_id
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = 'upsert', enqueued_at = EXCLUDED.enqueued_at, locked_at = NULL;
    ELSIF TG_TABLE_NAME = 'versions' THEN
        INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
        SELECT 'build', bv.build_id::text, 'upsert', now()
        FROM public.build_versions bv WHERE bv.version_id = target_id
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = 'upsert', enqueued_at = EXCLUDED.enqueued_at, locked_at = NULL;
    END IF;
    RETURN NULL;
END;
$$;
"""
_LEGACY_FIND_FUNCTION = """
CREATE FUNCTION public.find_restriction_ids(search_terms text[])
RETURNS TABLE(id smallint, build_category text, name text, type text)
LANGUAGE sql STABLE AS $$
    SELECT DISTINCT r.id, r.build_category, matched.name, r.type
    FROM public.restrictions r
    JOIN LATERAL (
        SELECT r.name WHERE r.name = ANY(search_terms)
        UNION
        SELECT a.alias FROM public.restriction_aliases a
        WHERE a.restriction_id = r.id AND a.alias = ANY(search_terms)
    ) matched ON true;
$$;
"""
_LEGACY_SYNC_FUNCTION = """
CREATE FUNCTION public.sync_new_restriction() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    restriction_name text;
    target_restriction_id int;
    restriction_category text;
    restriction_type text;
    json_key text;
BEGIN
    IF TG_TABLE_NAME = 'restrictions' THEN
        restriction_name := NEW.name;
        target_restriction_id := NEW.id;
        restriction_category := NEW.build_category;
        restriction_type := NEW.type;
    ELSE
        restriction_name := NEW.alias;
        target_restriction_id := NEW.restriction_id;
        SELECT build_category, type INTO restriction_category, restriction_type
        FROM public.restrictions WHERE id = NEW.restriction_id;
    END IF;
    json_key := CASE restriction_type
        WHEN 'component' THEN 'component_restrictions'
        WHEN 'wiring-placement' THEN 'wiring_placement_restrictions'
        WHEN 'miscellaneous' THEN 'miscellaneous_restrictions'
    END;
    IF json_key IS NULL THEN RETURN NULL; END IF;
    WITH affected AS (
        SELECT b.id
        FROM public.builds b
        WHERE b.category = restriction_category
          AND EXISTS (
              SELECT 1
              FROM jsonb_array_elements_text(
                  b.extra_info -> 'unknown_restrictions' -> json_key
              ) AS item(value)
              WHERE lower(item.value) = lower(restriction_name)
          )
    )
    INSERT INTO public.build_restrictions (build_id, restriction_id)
    SELECT id, target_restriction_id FROM affected
    ON CONFLICT DO NOTHING;
    RETURN NULL;
END;
$$;
"""


def upgrade() -> None:
    """Verify imported data, remove the legacy taxonomy, and rebuild projections."""
    _assert_taxonomy_parity()
    for trigger_name, table_name in _LEGACY_TABLE_TRIGGERS:
        op.execute(f'DROP TRIGGER IF EXISTS "{trigger_name}" ON public."{table_name}"')
    op.execute("DROP FUNCTION IF EXISTS public.find_restriction_ids(text[])")
    op.execute("DROP FUNCTION IF EXISTS public.sync_new_restriction()")

    metadata_function = _selected_entity(PGFunction, "enqueue_metadata_search_projection")
    for statement in metadata_function.to_sql_statement_create_or_replace():
        op.execute(statement)
    for trigger in _selected_entities(PGTrigger, _TAG_TRIGGER_NAMES):
        op.execute(trigger.to_sql_statement_create())

    op.execute(
        "DELETE FROM public.search_projection_queue "
        "WHERE resource_kind = 'metadata' AND (source_key LIKE 'restriction:%' OR source_key LIKE 'type:%')"
    )
    op.execute(
        "DELETE FROM public.search_documents "
        "WHERE resource_kind = 'metadata' AND (source_key LIKE 'restriction:%' OR source_key LIKE 'type:%')"
    )
    op.drop_table("build_restrictions")
    op.drop_table("build_types")
    op.drop_table("restriction_aliases")
    op.drop_table("restrictions")
    op.drop_table("types")
    _enqueue_rebuilds("taxonomy_cutover_complete")


def downgrade() -> None:
    """Reconstruct a writable legacy taxonomy projection from unified tags."""
    for trigger in reversed(_selected_entities(PGTrigger, _TAG_TRIGGER_NAMES)):
        op.execute(trigger.to_sql_statement_drop())
    _create_legacy_tables()
    _populate_legacy_tables()
    op.execute(_LEGACY_FIND_FUNCTION)
    op.execute(_LEGACY_SYNC_FUNCTION)
    op.execute(_LEGACY_METADATA_FUNCTION)
    _create_legacy_triggers()
    _enqueue_rebuilds("taxonomy_cutover_downgrade")


def _assert_taxonomy_parity() -> None:
    op.execute(
        r"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM public.restrictions r
                LEFT JOIN public.tag_definitions d ON d.stable_key = CASE
                    WHEN lower(trim(r.name)) = 'expandable' THEN 'expandable'
                    ELSE 'legacy_restriction_' || r.id::text
                END
                WHERE d.id IS NULL
            ) THEN RAISE EXCEPTION 'taxonomy cutover refused: restriction definitions are not fully imported';
            END IF;
            IF EXISTS (
                SELECT 1 FROM public.types t
                LEFT JOIN public.tag_definitions d ON d.stable_key = 'legacy_pattern_' || t.id::text
                WHERE d.id IS NULL
            ) THEN RAISE EXCEPTION 'taxonomy cutover refused: pattern definitions are not fully imported';
            END IF;
            IF EXISTS (
                SELECT 1 FROM public.restriction_aliases a
                JOIN public.restrictions r ON r.id = a.restriction_id
                JOIN public.tag_definitions d ON d.stable_key = CASE
                    WHEN lower(trim(r.name)) = 'expandable' THEN 'expandable'
                    ELSE 'legacy_restriction_' || r.id::text
                END
                LEFT JOIN public.tag_aliases ta
                    ON ta.tag_id = d.id
                   AND ta.normalized_alias = lower(regexp_replace(trim(a.alias), '\s+', ' ', 'g'))
                WHERE ta.tag_id IS NULL
            ) THEN RAISE EXCEPTION 'taxonomy cutover refused: restriction aliases are not fully imported';
            END IF;
            IF EXISTS (
                SELECT 1 FROM public.restrictions r
                JOIN public.tag_definitions d ON d.stable_key = CASE
                    WHEN lower(trim(r.name)) = 'expandable' THEN 'expandable'
                    ELSE 'legacy_restriction_' || r.id::text
                END
                LEFT JOIN public.tag_applicabilities a
                    ON a.tag_id = d.id AND a.build_kind = r.build_category
                WHERE r.build_category IS NOT NULL AND a.tag_id IS NULL
            ) OR EXISTS (
                SELECT 1 FROM public.types t
                JOIN public.tag_definitions d ON d.stable_key = 'legacy_pattern_' || t.id::text
                LEFT JOIN public.tag_applicabilities a
                    ON a.tag_id = d.id AND a.build_kind = t.build_category
                WHERE t.build_category IS NOT NULL AND a.tag_id IS NULL
            ) THEN RAISE EXCEPTION 'taxonomy cutover refused: applicability is not fully imported';
            END IF;
            IF EXISTS (
                SELECT 1 FROM public.build_restrictions br
                JOIN public.restrictions r ON r.id = br.restriction_id
                JOIN public.tag_definitions d ON d.stable_key = CASE
                    WHEN lower(trim(r.name)) = 'expandable' THEN 'expandable'
                    ELSE 'legacy_restriction_' || r.id::text
                END
                LEFT JOIN public.build_tag_assignments a ON a.build_id = br.build_id AND a.tag_id = d.id
                WHERE a.tag_id IS NULL
            ) OR EXISTS (
                SELECT 1 FROM public.build_types bt
                JOIN public.tag_definitions d ON d.stable_key = 'legacy_pattern_' || bt.type_id::text
                LEFT JOIN public.build_tag_assignments a ON a.build_id = bt.build_id AND a.tag_id = d.id
                WHERE a.tag_id IS NULL
            ) THEN RAISE EXCEPTION 'taxonomy cutover refused: build assignments are not fully imported';
            END IF;
        END;
        $$;
        """
    )


def _create_legacy_tables() -> None:
    op.create_table(
        "restrictions",
        sa.Column("id", sa.SmallInteger(), sa.Identity(), nullable=False),
        sa.Column("build_category", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("type", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="restrictions_pkey"),
        sa.UniqueConstraint("name", name="restrictions_name_key"),
    )
    op.create_table(
        "types",
        sa.Column("id", sa.SmallInteger(), sa.Identity(), nullable=False),
        sa.Column("build_category", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="types_pkey"),
        sa.UniqueConstraint("name", name="types_name_key"),
    )
    op.create_table(
        "restriction_aliases",
        sa.Column("restriction_id", sa.SmallInteger(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("alias", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["restriction_id"],
            ["restrictions.id"],
            name="restriction_aliases_restriction_id_fkey",
            onupdate="CASCADE",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("alias", name="restriction_aliases_pkey"),
    )
    op.create_index("restriction_aliases_restriction_id_idx", "restriction_aliases", ["restriction_id"])
    op.create_table(
        "build_restrictions",
        sa.Column("build_id", sa.BigInteger(), nullable=False),
        sa.Column("restriction_id", sa.SmallInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["build_id"], ["builds.id"], name="build_restrictions_build_id_fkey", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["restriction_id"],
            ["restrictions.id"],
            name="build_restrictions_restriction_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("build_id", "restriction_id", name="build_restrictions_pkey"),
    )
    op.create_table(
        "build_types",
        sa.Column("build_id", sa.BigInteger(), nullable=False),
        sa.Column("type_id", sa.SmallInteger(), nullable=False),
        sa.ForeignKeyConstraint(["build_id"], ["builds.id"], name="build_types_build_id_fkey", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["type_id"], ["types.id"], name="build_types_type_id_fkey", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("build_id", "type_id", name="build_types_pkey"),
    )


def _populate_legacy_tables() -> None:
    op.execute(
        """
        INSERT INTO public.restrictions (build_category, name, type)
        SELECT min(a.build_kind), d.display_name, d.restriction_type
        FROM public.tag_definitions d
        LEFT JOIN public.tag_applicabilities a ON a.tag_id = d.id
        WHERE d.authority = 'official' AND d.semantic_kind = 'restriction'
        GROUP BY d.id, d.display_name, d.restriction_type
        ON CONFLICT (name) DO NOTHING;
        INSERT INTO public.types (build_category, name)
        SELECT min(a.build_kind), d.display_name
        FROM public.tag_definitions d
        LEFT JOIN public.tag_applicabilities a ON a.tag_id = d.id
        WHERE d.authority = 'official' AND d.semantic_kind = 'pattern'
        GROUP BY d.id, d.display_name
        ON CONFLICT (name) DO NOTHING;
        INSERT INTO public.restriction_aliases (restriction_id, alias, created_at)
        SELECT r.id, a.alias, a.created_at
        FROM public.tag_aliases a
        JOIN public.tag_definitions d ON d.id = a.tag_id
        JOIN public.restrictions r ON r.name = d.display_name
        WHERE d.authority = 'official' AND d.semantic_kind = 'restriction'
        ON CONFLICT (alias) DO NOTHING;
        INSERT INTO public.build_restrictions (build_id, restriction_id)
        SELECT assignment.build_id, r.id
        FROM public.build_tag_assignments assignment
        JOIN public.tag_definitions d ON d.id = assignment.tag_id
        JOIN public.restrictions r ON r.name = d.display_name
        WHERE d.authority = 'official' AND d.semantic_kind = 'restriction'
        ON CONFLICT DO NOTHING;
        INSERT INTO public.build_types (build_id, type_id)
        SELECT assignment.build_id, t.id
        FROM public.build_tag_assignments assignment
        JOIN public.tag_definitions d ON d.id = assignment.tag_id
        JOIN public.types t ON t.name = d.display_name
        WHERE d.authority = 'official' AND d.semantic_kind = 'pattern'
        ON CONFLICT DO NOTHING;
        """
    )


def _create_legacy_triggers() -> None:
    statements = (
        "CREATE TRIGGER trg_sync_on_tag AFTER INSERT ON public.restrictions "
        "FOR EACH ROW EXECUTE FUNCTION public.sync_new_restriction()",
        "CREATE TRIGGER trg_sync_on_tag_alias AFTER INSERT ON public.restriction_aliases "
        "FOR EACH ROW EXECUTE FUNCTION public.sync_new_restriction()",
        "CREATE TRIGGER build_restrictions_enqueue_search AFTER INSERT OR DELETE OR UPDATE "
        "ON public.build_restrictions FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection()",
        "CREATE TRIGGER build_types_enqueue_search AFTER INSERT OR DELETE OR UPDATE "
        "ON public.build_types FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection()",
        "CREATE TRIGGER restrictions_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.restrictions "
        "FOR EACH ROW EXECUTE FUNCTION public.enqueue_metadata_search_projection()",
        "CREATE TRIGGER restriction_aliases_enqueue_search AFTER INSERT OR DELETE OR UPDATE "
        "ON public.restriction_aliases FOR EACH ROW EXECUTE FUNCTION public.enqueue_metadata_search_projection()",
        "CREATE TRIGGER types_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.types "
        "FOR EACH ROW EXECUTE FUNCTION public.enqueue_metadata_search_projection()",
        "CREATE TRIGGER build_restrictions_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE "
        "ON public.build_restrictions FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync()",
        "CREATE TRIGGER build_types_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE "
        "ON public.build_types FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync()",
    )
    for statement in statements:
        op.execute(statement)


def _enqueue_rebuilds(reason: str) -> None:
    op.execute(
        """
        INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
        SELECT 'build', id::text, 'upsert', now() FROM public.builds
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = 'upsert', enqueued_at = EXCLUDED.enqueued_at,
            attempts = 0, locked_at = NULL, last_error = NULL;
        INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
        SELECT 'metadata', 'tag:' || id::text, 'upsert', now()
        FROM public.tag_definitions WHERE moderation_status = 'approved'
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = 'upsert', enqueued_at = EXCLUDED.enqueued_at,
            attempts = 0, locked_at = NULL, last_error = NULL;
        INSERT INTO public.discord_sync_queue
            (resource_kind, source_key, action, enqueued_at, claimed_at, dead_at, attempts, last_error)
        SELECT 'build', id::text, 'refresh', now(), NULL, NULL, 0, NULL FROM public.builds
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = 'refresh', enqueued_at = EXCLUDED.enqueued_at, claimed_at = NULL,
            dead_at = NULL, attempts = 0, last_error = NULL;
        """
    )
    op.execute(
        sa.text(
            """
            INSERT INTO public.record_recompute_queue
                (scope_key, build_kind, build_id, reasons, enqueued_at)
            SELECT lower(category), lower(category), NULL, jsonb_build_array(:reason), now()
            FROM public.builds WHERE category IS NOT NULL
            GROUP BY lower(category)
            ON CONFLICT (scope_key) DO UPDATE
            SET reasons = public.record_recompute_queue.reasons || EXCLUDED.reasons,
                enqueued_at = EXCLUDED.enqueued_at, attempts = 0, locked_at = NULL, last_error = NULL
            """
        ).bindparams(reason=reason)
    )


EntityT = TypeVar("EntityT", bound=ReplaceableEntity)


def _selected_entities(entity_type: type[EntityT], names: set[str]) -> list[EntityT]:
    return [
        entity
        for entity in alembic_util_entities()
        if isinstance(entity, entity_type) and entity.signature.partition("(")[0] in names
    ]


def _selected_entity(entity_type: type[EntityT], name: str) -> EntityT:
    matches = _selected_entities(entity_type, {name})
    if len(matches) != 1:
        msg = f"Expected exactly one managed {name} entity, found {len(matches)}"
        raise RuntimeError(msg)
    return matches[0]
