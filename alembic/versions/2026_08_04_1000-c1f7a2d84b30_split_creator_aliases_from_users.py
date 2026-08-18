"""split creator aliases from users

Revision ID: c1f7a2d84b30
Revises: a6c14ee7529f
Create Date: 2026-08-04 10:00:00+00:00
"""

from collections.abc import Sequence
from typing import TypeVar

import sqlalchemy as sa
from alembic_utils.pg_function import PGFunction
from alembic_utils.pg_trigger import PGTrigger
from alembic_utils.replaceable_entity import ReplaceableEntity

from alembic import op
from squid.persistence.alembic_entities import alembic_util_entities

revision: str = "c1f7a2d84b30"
down_revision: str | Sequence[str] | None = "a6c14ee7529f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FUNCTION_NAMES = {"enqueue_metadata_search_projection"}


def upgrade() -> None:
    """Move creator credits out of `users` into their own claimable table.

    Before this revision a build submission inserted a bare `users` row for
    every creator name it did not recognise, so a name typed by a third party
    was indistinguishable from an account and could never be reconciled with
    one. Credits now live in `creator_aliases`, which an account claims.
    """
    op.create_table(
        "creator_aliases",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "normalized_name",
            sa.Text(),
            sa.Computed("lower(btrim(name))", persisted=True),
            nullable=False,
        ),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_method", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="creator_aliases_user_id_fkey", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="creator_aliases_pkey"),
        sa.UniqueConstraint("normalized_name", name="creator_aliases_normalized_name_key"),
        sa.CheckConstraint("(user_id IS NULL) = (claimed_at IS NULL)", name="creator_aliases_claim_complete"),
        sa.CheckConstraint("(user_id IS NULL) = (claim_method IS NULL)", name="creator_aliases_claim_method_complete"),
        sa.CheckConstraint(
            "claim_method IS NULL OR claim_method IN ('verified_ign', 'staff_approved', 'migrated')",
            name="creator_aliases_claim_method_check",
        ),
        comment="A creator name credited on a build, optionally claimed by an account.",
    )
    op.create_table(
        "creator_alias_claims",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("alias_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by_discord_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["alias_id"], ["creator_aliases.id"], name="creator_alias_claims_alias_id_fkey", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="creator_alias_claims_user_id_fkey", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="creator_alias_claims_pkey"),
        sa.CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="creator_alias_claims_status_check"),
        sa.CheckConstraint(
            "(status = 'pending') = (resolved_at IS NULL)", name="creator_alias_claims_resolution_complete"
        ),
        comment="A user's request to be credited under a creator alias, pending staff review.",
    )
    op.create_index(
        "creator_alias_claims_one_pending_per_user",
        "creator_alias_claims",
        ["alias_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )

    # One alias per distinct name, over every users row that is credited on a
    # build or that only ever existed because of one. `DISTINCT ON` keeps the
    # earliest spelling when rows differ only by case.
    op.execute(
        """
        INSERT INTO public.creator_aliases (name)
        SELECT DISTINCT ON (lower(btrim(u.ign))) btrim(u.ign)
        FROM public.users u
        WHERE u.ign IS NOT NULL
          AND btrim(u.ign) <> ''
          AND (
              EXISTS (SELECT 1 FROM public.build_creators bc WHERE bc.user_id = u.id)
              OR (u.discord_id IS NULL AND u.minecraft_uuid IS NULL)
          )
        ORDER BY lower(btrim(u.ign)), u.id
        """
    )

    # An account that was already credited keeps its credit.
    op.execute(
        """
        UPDATE public.creator_aliases ca
        SET user_id = src.user_id, claimed_at = now(), claim_method = 'migrated'
        FROM (
            SELECT DISTINCT ON (lower(btrim(u.ign))) lower(btrim(u.ign)) AS normalized_name, u.id AS user_id
            FROM public.users u
            WHERE u.ign IS NOT NULL
              AND btrim(u.ign) <> ''
              AND (u.discord_id IS NOT NULL OR u.minecraft_uuid IS NOT NULL)
            ORDER BY lower(btrim(u.ign)), u.id
        ) src
        WHERE ca.normalized_name = src.normalized_name
        """
    )

    op.add_column("build_creators", sa.Column("alias_id", sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE public.build_creators bc
        SET alias_id = ca.id
        FROM public.users u
        JOIN public.creator_aliases ca ON ca.normalized_name = lower(btrim(u.ign))
        WHERE bc.user_id = u.id
        """
    )
    # Credits whose user row had no usable name cannot be attributed to anyone.
    op.execute("DELETE FROM public.build_creators WHERE alias_id IS NULL")
    # Case-variant rows collapse onto one alias, which would duplicate the PK.
    op.execute(
        """
        DELETE FROM public.build_creators bc
        USING public.build_creators keep
        WHERE bc.build_id = keep.build_id
          AND bc.alias_id = keep.alias_id
          AND bc.user_id > keep.user_id
        """
    )

    op.drop_constraint("build_creators_pkey", "build_creators", type_="primary")
    op.drop_constraint("build_creators_user_id_fkey", "build_creators", type_="foreignkey")
    op.drop_column("build_creators", "user_id")
    op.alter_column("build_creators", "alias_id", nullable=False)
    op.create_primary_key("build_creators_pkey", "build_creators", ["build_id", "alias_id"])
    op.create_foreign_key(
        "build_creators_alias_id_fkey",
        "build_creators",
        "creator_aliases",
        ["alias_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_table_comment(
        "build_creators",
        "Association table between builds and the creator names credited on them.",
        existing_comment="Association table between builds and their creators.",
    )

    # Rows that only ever represented a typed-in name are now unreferenced:
    # build_creators no longer points at users, and nothing else can reach a
    # row with neither identifier.
    op.execute("DELETE FROM public.users WHERE discord_id IS NULL AND minecraft_uuid IS NULL")

    op.execute("DROP TRIGGER IF EXISTS users_enqueue_search ON public.users")
    for entity in _selected_entities(PGFunction, _FUNCTION_NAMES):
        # `to_sql_statement_create_or_replace` yields several statements.
        for statement in entity.to_sql_statement_create_or_replace():
            op.execute(statement)
    for entity in _selected_entities(PGTrigger, {"creator_aliases_enqueue_search"}):
        op.execute(entity.to_sql_statement_create())

    # Creator documents were keyed on users.id and carried a discord_id; both
    # are wrong now, so drop them and reproject from the new table.
    op.execute("DELETE FROM public.search_documents WHERE resource_kind = 'metadata' AND source_key LIKE 'creator:%'")
    op.execute(
        """
        INSERT INTO public.search_projection_queue (resource_kind, source_key, action)
        SELECT 'metadata', 'creator:' || id::text, 'upsert' FROM public.creator_aliases
        UNION ALL
        SELECT 'build', id::text, 'upsert' FROM public.builds
        ON CONFLICT (resource_kind, source_key) DO NOTHING
        """
    )


def downgrade() -> None:
    """Fold creator credits back into `users`."""
    op.execute("DROP TRIGGER IF EXISTS creator_aliases_enqueue_search ON public.creator_aliases")

    op.add_column("build_creators", sa.Column("user_id", sa.Integer(), nullable=True))
    # Recreate a users row per alias that no account holds.
    op.execute(
        """
        INSERT INTO public.users (ign)
        SELECT ca.name FROM public.creator_aliases ca WHERE ca.user_id IS NULL
        """
    )
    op.execute(
        """
        UPDATE public.build_creators bc
        SET user_id = COALESCE(ca.user_id, u.id)
        FROM public.creator_aliases ca
        LEFT JOIN public.users u ON u.ign = ca.name AND u.discord_id IS NULL AND u.minecraft_uuid IS NULL
        WHERE bc.alias_id = ca.id
        """
    )
    op.execute("DELETE FROM public.build_creators WHERE user_id IS NULL")

    op.drop_constraint("build_creators_alias_id_fkey", "build_creators", type_="foreignkey")
    op.drop_constraint("build_creators_pkey", "build_creators", type_="primary")
    op.drop_column("build_creators", "alias_id")
    op.alter_column("build_creators", "user_id", nullable=False)
    op.create_primary_key("build_creators_pkey", "build_creators", ["build_id", "user_id"])
    op.create_foreign_key("build_creators_user_id_fkey", "build_creators", "users", ["user_id"], ["id"])

    op.create_table_comment(
        "build_creators",
        "Association table between builds and their creators.",
        existing_comment="Association table between builds and the creator names credited on them.",
    )
    op.drop_index("creator_alias_claims_one_pending_per_user", table_name="creator_alias_claims")
    op.drop_table("creator_alias_claims")
    op.drop_table("creator_aliases")

    # Restore the users-keyed body verbatim; replacing it with a stub would
    # silently break projection for restrictions, types, and versions too.
    op.execute(_LEGACY_METADATA_FUNCTION)
    op.execute(
        "CREATE TRIGGER users_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.users "
        "FOR EACH ROW EXECUTE FUNCTION public.enqueue_metadata_search_projection()"
    )
    op.execute("DELETE FROM public.search_documents WHERE resource_kind = 'metadata' AND source_key LIKE 'creator:%'")
    op.execute(
        """
        INSERT INTO public.search_projection_queue (resource_kind, source_key, action)
        SELECT 'metadata', 'creator:' || id::text, 'upsert' FROM public.users WHERE ign IS NOT NULL
        ON CONFLICT (resource_kind, source_key) DO NOTHING
        """
    )


_LEGACY_METADATA_FUNCTION = """
CREATE OR REPLACE FUNCTION public.enqueue_metadata_search_projection() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    target_id bigint;
    target_kind text;
    target_action text := 'upsert';
BEGIN
    target_kind := CASE TG_TABLE_NAME
        WHEN 'restrictions' THEN 'restriction'
        WHEN 'restriction_aliases' THEN 'restriction'
        WHEN 'types' THEN 'type'
        WHEN 'users' THEN 'creator'
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
    SET action = EXCLUDED.action,
        enqueued_at = EXCLUDED.enqueued_at,
        attempts = 0,
        locked_at = NULL,
        last_error = NULL;

    IF TG_TABLE_NAME IN ('restrictions', 'restriction_aliases') THEN
        INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
        SELECT 'build', br.build_id::text, 'upsert', now()
        FROM public.build_restrictions br
        WHERE br.restriction_id = target_id
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = 'upsert', enqueued_at = EXCLUDED.enqueued_at, locked_at = NULL;
    ELSIF TG_TABLE_NAME = 'types' THEN
        INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
        SELECT 'build', bt.build_id::text, 'upsert', now()
        FROM public.build_types bt
        WHERE bt.type_id = target_id
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = 'upsert', enqueued_at = EXCLUDED.enqueued_at, locked_at = NULL;
    ELSIF TG_TABLE_NAME = 'users' THEN
        INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
        SELECT 'build', bc.build_id::text, 'upsert', now()
        FROM public.build_creators bc
        WHERE bc.user_id = target_id
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = 'upsert', enqueued_at = EXCLUDED.enqueued_at, locked_at = NULL;
    ELSIF TG_TABLE_NAME = 'versions' THEN
        INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
        SELECT 'build', bv.build_id::text, 'upsert', now()
        FROM public.build_versions bv
        WHERE bv.version_id = target_id
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = 'upsert', enqueued_at = EXCLUDED.enqueued_at, locked_at = NULL;
    END IF;
    RETURN NULL;
END;
$$
"""


EntityT = TypeVar("EntityT", bound=ReplaceableEntity)


def _selected_entities(entity_type: type[EntityT], names: set[str]) -> list[EntityT]:
    return [
        entity
        for entity in alembic_util_entities()
        if isinstance(entity, entity_type) and entity.signature.partition("(")[0] in names
    ]
