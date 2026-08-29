"""Draw sync generations from a sequence and track bot-owned Discord posts

`ClaimedRowQueue.complete` deletes an acknowledged queue row, so a per-row
`generation` counter restarted at 1 on the next enqueue. The AFTER INSERT
projection trigger then wrote that 1 into `messages.desired_revision`, and any
message already acknowledged at a higher generation violated the check constraint
`messages_projection_revisions_valid` — inside the statement that enqueued the
work, so it aborted the user's build edit rather than merely stalling a refresh.

Drawing the generation from a sequence fixes it at the root: sequence values are
exempt from rollback and are never reused, which is exactly what a staleness token
needs. `bump_discord_sync_generation` and its trigger become dead weight.

`discord_posts` arrives alongside it, holding only applied state. What a post
*should* look like stays on the queue row, so staleness is a join rather than a
desired revision copied onto every post. Its partial unique index over
(resource_kind, resource_key, channel_id) is what makes "one live post per resource
per channel" a database guarantee instead of three hand-rolled idempotency checks.

Revision ID: c2d3e4f5a6b1
Revises: b1c2d3e4f5a6
Create Date: 2026-08-15 14:00:00.000000+00:00
"""

from collections.abc import Sequence
from typing import TypeVar

import sqlalchemy as sa
from alembic_utils.pg_function import PGFunction
from alembic_utils.replaceable_entity import ReplaceableEntity

import squid.persistence.types
from alembic import op
from squid.persistence.alembic_entities import alembic_util_entities

revision: str = "c2d3e4f5a6b1"
down_revision: str | Sequence[str] | None = "b1c2d3e4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REPLACED_FUNCTIONS = {"enqueue_discord_sync", "enqueue_metadata_search_projection"}


def upgrade() -> None:
    """Apply this revision."""
    op.execute("CREATE SEQUENCE public.discord_sync_generation_seq AS bigint START WITH 1 INCREMENT BY 1")
    # Start above every generation already handed out, so no existing acknowledgement
    # can outrank a value the sequence is about to produce.
    op.execute(
        """
        SELECT setval(
            'public.discord_sync_generation_seq',
            GREATEST(
                (SELECT COALESCE(max(generation), 0) FROM public.discord_sync_queue),
                (SELECT COALESCE(max(desired_revision), 0) FROM public.messages),
                (SELECT COALESCE(max(applied_revision), 0) FROM public.messages),
                1
            )
        )
        """
    )
    op.alter_column(
        "discord_sync_queue",
        "generation",
        existing_type=sa.BigInteger(),
        server_default=sa.text("nextval('discord_sync_generation_seq')"),
        existing_nullable=False,
    )

    op.create_table(
        "discord_posts",
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("resource_kind", sa.Text(), nullable=False),
        sa.Column("resource_key", sa.Text(), nullable=False),
        sa.Column("surface", sa.Text(), nullable=False),
        sa.Column("applied_revision", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "posted_at",
            squid.persistence.types.InstantUTC(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("rendered_at", squid.persistence.types.InstantUTC(timezone=True), nullable=True),
        sa.Column("suppressed_at", squid.persistence.types.InstantUTC(timezone=True), nullable=True),
        sa.CheckConstraint(
            "resource_kind IN ('build', 'vote_session', 'starboard_entry')",
            name="discord_posts_resource_kind_check",
        ),
        sa.CheckConstraint(
            "surface IN ('build_card', 'build_review', 'starboard_entry')",
            name="discord_posts_surface_check",
        ),
        sa.CheckConstraint("applied_revision >= 0", name="discord_posts_applied_revision_check"),
        sa.ForeignKeyConstraint(
            ["message_id"], ["messages.id"], name="discord_posts_message_id_fkey", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("message_id"),
        comment=(
            "A Discord message the bot owns and keeps rendered for some resource.\n\n"
            "Holds only applied state. What the post *should* look like lives in the matching\n"
            "`discord_sync_queue` row, so staleness is `applied_revision < queue.generation`\n"
            "rather than a desired revision copied onto every post by a trigger."
        ),
    )
    op.create_index(
        "discord_posts_resource_channel_key",
        "discord_posts",
        ["resource_kind", "resource_key", "channel_id"],
        unique=True,
        postgresql_where=sa.text("suppressed_at IS NULL"),
    )
    op.create_index("discord_posts_resource_idx", "discord_posts", ["resource_kind", "resource_key"], unique=False)

    op.execute("DROP TRIGGER IF EXISTS discord_sync_queue_bump_generation ON public.discord_sync_queue")
    op.execute("DROP FUNCTION IF EXISTS public.bump_discord_sync_generation()")
    for entity in _selected_entities(PGFunction, _REPLACED_FUNCTIONS):
        # `to_sql_statement_create_or_replace` yields several statements: alembic_utils
        # renames the live function aside, creates the new one, then drops the old.
        for statement in entity.to_sql_statement_create_or_replace():
            op.execute(statement)


def downgrade() -> None:
    """Revert this revision when the operation is safe."""
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.bump_discord_sync_generation() RETURNS trigger
            LANGUAGE plpgsql
            AS $$
        BEGIN
            NEW.generation := OLD.generation + 1;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER discord_sync_queue_bump_generation BEFORE UPDATE OF enqueued_at "
        "ON public.discord_sync_queue FOR EACH ROW "
        "WHEN (OLD.enqueued_at IS DISTINCT FROM NEW.enqueued_at) "
        "EXECUTE FUNCTION public.bump_discord_sync_generation()"
    )
    op.execute(_ENQUEUE_DISCORD_SYNC_WITHOUT_SEQUENCE)
    op.execute(_ENQUEUE_METADATA_SEARCH_PROJECTION_WITHOUT_SEQUENCE)

    op.drop_index("discord_posts_resource_idx", table_name="discord_posts")
    op.drop_index("discord_posts_resource_channel_key", table_name="discord_posts")
    op.drop_table("discord_posts")

    op.alter_column(
        "discord_sync_queue",
        "generation",
        existing_type=sa.BigInteger(),
        server_default=sa.text("1"),
        existing_nullable=False,
    )
    op.execute("DROP SEQUENCE IF EXISTS public.discord_sync_generation_seq")


EntityT = TypeVar("EntityT", bound=ReplaceableEntity)


def _selected_entities(entity_type: type[EntityT], names: set[str]) -> list[EntityT]:
    return [
        entity
        for entity in alembic_util_entities()
        if isinstance(entity, entity_type) and entity.signature.partition("(")[0] in names
    ]


_ENQUEUE_DISCORD_SYNC_WITHOUT_SEQUENCE = """
CREATE OR REPLACE FUNCTION public.enqueue_discord_sync() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    target_kind text;
    target_key bigint;
    target_action text := 'refresh';
BEGIN
    IF TG_TABLE_NAME = 'vote_sessions' THEN
        target_kind := 'vote_session';
        target_key := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;
        IF TG_OP = 'DELETE' THEN target_action := 'delete'; END IF;
    ELSIF TG_TABLE_NAME = 'votes' THEN
        target_kind := 'vote_session';
        target_key := CASE WHEN TG_OP = 'DELETE' THEN OLD.vote_session_id ELSE NEW.vote_session_id END;
        IF NOT EXISTS (SELECT 1 FROM public.vote_sessions WHERE id = target_key) THEN RETURN NULL; END IF;
    ELSIF TG_TABLE_NAME = 'builds' THEN
        target_kind := 'build';
        target_key := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;
        IF TG_OP = 'DELETE' THEN target_action := 'delete'; END IF;
    ELSE
        target_kind := 'build';
        target_key := CASE WHEN TG_OP = 'DELETE' THEN OLD.build_id ELSE NEW.build_id END;
        IF NOT EXISTS (SELECT 1 FROM public.builds WHERE id = target_key) THEN RETURN NULL; END IF;
    END IF;

    INSERT INTO public.discord_sync_queue
        (resource_kind, source_key, action, enqueued_at, claimed_at, dead_at, attempts, last_error)
    VALUES (target_kind, target_key::text, target_action, now(), NULL, NULL, 0, NULL)
    ON CONFLICT (resource_kind, source_key) DO UPDATE
    SET action = EXCLUDED.action,
        enqueued_at = EXCLUDED.enqueued_at,
        claimed_at = NULL,
        dead_at = NULL,
        attempts = 0,
        last_error = NULL;
    RETURN NULL;
END;
$$
"""

_ENQUEUE_METADATA_SEARCH_PROJECTION_WITHOUT_SEQUENCE = """
CREATE OR REPLACE FUNCTION public.enqueue_metadata_search_projection() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    target_id bigint;
    target_kind text;
    target_action text := 'upsert';
BEGIN
    target_kind := CASE TG_TABLE_NAME
        WHEN 'tag_definitions' THEN 'tag'
        WHEN 'tag_aliases' THEN 'tag'
        WHEN 'creator_aliases' THEN 'creator'
        WHEN 'versions' THEN 'version'
    END;
    IF TG_TABLE_NAME = 'tag_aliases' THEN
        target_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.tag_id ELSE NEW.tag_id END;
    ELSE
        target_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;
    END IF;
    IF TG_OP = 'DELETE' AND TG_TABLE_NAME <> 'tag_aliases' THEN
        target_action := 'delete';
    END IF;

    INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
    VALUES ('metadata', target_kind || ':' || target_id::text, target_action, now())
    ON CONFLICT (resource_kind, source_key) DO UPDATE
    SET action = EXCLUDED.action,
        enqueued_at = EXCLUDED.enqueued_at,
        attempts = 0,
        locked_at = NULL,
        dead_at = NULL,
        last_error = NULL;

    IF TG_TABLE_NAME IN ('tag_definitions', 'tag_aliases') THEN
        INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
        SELECT 'build', assignment.build_id::text, 'upsert', now()
        FROM public.build_tag_assignments assignment
        WHERE assignment.tag_id = target_id
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = 'upsert',
            enqueued_at = EXCLUDED.enqueued_at,
            attempts = 0,
            locked_at = NULL,
            dead_at = NULL,
            last_error = NULL;

        INSERT INTO public.discord_sync_queue
            (resource_kind, source_key, action, enqueued_at, claimed_at, dead_at, attempts, last_error)
        SELECT 'build', assignment.build_id::text, 'refresh', now(), NULL, NULL, 0, NULL
        FROM public.build_tag_assignments assignment
        WHERE assignment.tag_id = target_id
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = EXCLUDED.action,
            enqueued_at = EXCLUDED.enqueued_at,
            claimed_at = NULL,
            dead_at = NULL,
            attempts = 0,
            last_error = NULL;
    ELSIF TG_TABLE_NAME = 'creator_aliases' THEN
        INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
        SELECT 'build', bc.build_id::text, 'upsert', now()
        FROM public.build_creators bc
        WHERE bc.alias_id = target_id
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = 'upsert', enqueued_at = EXCLUDED.enqueued_at,
            attempts = 0, locked_at = NULL, dead_at = NULL, last_error = NULL;
    ELSIF TG_TABLE_NAME = 'versions' THEN
        INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
        SELECT 'build', bv.build_id::text, 'upsert', now()
        FROM public.build_versions bv
        WHERE bv.version_id = target_id
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = 'upsert', enqueued_at = EXCLUDED.enqueued_at,
            attempts = 0, locked_at = NULL, dead_at = NULL, last_error = NULL;
    END IF;
    RETURN NULL;
END;
$$
"""
