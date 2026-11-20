"""Add optimistic build revisions.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-08-10 12:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REVISION_UPDATE = "SET    extra_info = a.new_extra,\n               revision = b.revision + 1"
_LEGACY_UPDATE = "SET    extra_info = a.new_extra"
_RESTRICTION_SYNC_SQL = """
CREATE OR REPLACE FUNCTION public.sync_new_restriction() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    b_restriction text;
    b_restriction_id int;
    r_category text;
    r_type text;
    json_key text;
BEGIN
    IF TG_TABLE_NAME = 'restrictions' THEN
        b_restriction := NEW.name;
        b_restriction_id := NEW.id;
        r_category := NEW.build_category;
        r_type := NEW.type;
    ELSIF TG_TABLE_NAME = 'restriction_aliases' THEN
        b_restriction := NEW.alias;
        b_restriction_id := NEW.restriction_id;
        SELECT r.build_category, r.type INTO r_category, r_type
        FROM restrictions r WHERE r.id = NEW.restriction_id;
    ELSE
        RAISE EXCEPTION 'sync_new_restriction() fired by unexpected table %', TG_TABLE_NAME;
    END IF;
    json_key := CASE r_type
        WHEN 'component' THEN 'component_restrictions'
        WHEN 'wiring-placement' THEN 'wiring_placement_restrictions'
        WHEN 'miscellaneous' THEN 'miscellaneous_restrictions'
    END;
    IF json_key IS NULL THEN
        RETURN NULL;
    END IF;
    WITH affected AS (
        SELECT b.id,
               (
                   WITH elems AS (
                       SELECT jsonb_array_elements_text(
                           b.extra_info -> 'unknown_restrictions' -> json_key
                       ) AS val
                   ),
                   kept AS (
                       SELECT jsonb_agg(to_jsonb(val)) AS arr
                       FROM elems WHERE lower(val) <> lower(b_restriction)
                   )
                   SELECT CASE
                       WHEN (SELECT arr FROM kept) IS NULL THEN
                           CASE
                               WHEN ((b.extra_info -> 'unknown_restrictions') - json_key) = '{}'::jsonb
                               THEN b.extra_info - 'unknown_restrictions'
                               ELSE jsonb_set(
                                   b.extra_info,
                                   '{unknown_restrictions}',
                                   (b.extra_info -> 'unknown_restrictions') - json_key,
                                   TRUE
                               )
                           END
                       ELSE jsonb_set(
                           b.extra_info,
                           ARRAY['unknown_restrictions', json_key],
                           (SELECT arr FROM kept),
                           TRUE
                       )
                   END
               ) AS new_extra
        FROM builds b
        WHERE b.category = r_category
          AND EXISTS (
              SELECT 1
              FROM jsonb_array_elements_text(
                  b.extra_info -> 'unknown_restrictions' -> json_key
              ) AS t(val)
              WHERE lower(val) = lower(b_restriction)
          )
    ),
    changed AS (
        UPDATE builds b
        SET    extra_info = a.new_extra,
               revision = b.revision + 1
        FROM affected a
        WHERE b.id = a.id
        RETURNING b.id
    )
    INSERT INTO build_restrictions (build_id, restriction_id)
    SELECT id, b_restriction_id FROM changed
    ON CONFLICT DO NOTHING;
    RETURN NULL;
END;
$$;
"""


def upgrade() -> None:
    """Add and backfill a monotonic build aggregate revision."""
    op.add_column(
        "builds",
        sa.Column("revision", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
    )
    op.execute(
        """
        UPDATE builds b
        SET revision = history.revision
        FROM (
            SELECT build_id, GREATEST(max(version)::bigint + 1, 1) AS revision
            FROM build_edit_history
            GROUP BY build_id
        ) history
        WHERE b.id = history.build_id
        """
    )
    op.create_check_constraint("builds_revision_positive", "builds", "revision > 0")
    op.execute(_restriction_sync_sql())


def downgrade() -> None:
    """Remove build revisions and restore the legacy restriction function."""
    op.execute(_restriction_sync_sql().replace(_REVISION_UPDATE, _LEGACY_UPDATE))
    op.drop_constraint("builds_revision_positive", "builds", type_="check")
    op.drop_column("builds", "revision")


def _restriction_sync_sql() -> str:
    return _RESTRICTION_SYNC_SQL
