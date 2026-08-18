"""Move starboard mirrors onto discord_posts

The last of the parallel message-tracking schemes. A starboard entry remembered its
own mirrored post in `posted_message_id`/`posted_channel_id`, decided send, update,
remove or noop from that memory, and healed a hand-deleted post by clearing the
columns and rescheduling itself.

All of that is what `discord_posts` and the reconcile loop already do for every other
surface. The entry keeps only its score; whether a post should exist is derived from
that score and the posts that are actually there.

Score changes and board configuration changes now enqueue durable work, so a mirror
survives a restart mid-update rather than depending on an in-process debouncer.

Revision ID: a6b1c2d3e4f5
Revises: f5a6b1c2d3e4
Create Date: 2026-08-15 18:00:00.000000+00:00
"""

from collections.abc import Sequence
from typing import TypeVar

import sqlalchemy as sa
from alembic_utils.pg_function import PGFunction
from alembic_utils.pg_trigger import PGTrigger
from alembic_utils.replaceable_entity import ReplaceableEntity

from alembic import op
from squid.persistence.alembic_entities import alembic_util_entities

revision: str = "a6b1c2d3e4f5"
down_revision: str | Sequence[str] | None = "f5a6b1c2d3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FUNCTION_NAMES = {"enqueue_starboard_sync"}
_TRIGGER_NAMES = {
    "starboard_entries_enqueue_discord_sync",
    "starboards_enqueue_discord_sync",
    "starboard_origin_messages_enqueue_discord_sync",
}


def upgrade() -> None:
    """Apply this revision."""
    op.drop_constraint("discord_sync_queue_resource_kind_check", "discord_sync_queue", type_="check")
    op.create_check_constraint(
        "discord_sync_queue_resource_kind_check",
        "discord_sync_queue",
        "resource_kind IN ('build', 'vote_session', 'starboard_entry')",
    )

    # Adopt the mirrors already posted so the reconciler edits them rather than
    # posting a second copy beside each one.
    op.execute(
        """
        INSERT INTO messages (id, server_id, channel_id, author_id, observed_at)
        SELECT e.posted_message_id, s.guild_id, e.posted_channel_id, 0, now()
        FROM starboard_entries e
        JOIN starboards s ON s.id = e.starboard_id
        WHERE e.posted_message_id IS NOT NULL AND e.posted_channel_id IS NOT NULL
        ON CONFLICT DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO discord_posts (
            message_id, channel_id, resource_kind, resource_key, surface, applied_revision, posted_at, rendered_at
        )
        SELECT
            e.posted_message_id,
            e.posted_channel_id,
            'starboard_entry',
            e.starboard_id || ':' || e.origin_message_id,
            'starboard_entry',
            0,
            COALESCE(e.first_posted_at, now()),
            e.updated_at
        FROM starboard_entries e
        WHERE e.posted_message_id IS NOT NULL AND e.posted_channel_id IS NOT NULL
        ON CONFLICT DO NOTHING
        """
    )

    op.drop_index("starboard_entries_posted_message_key", table_name="starboard_entries")
    op.drop_column("starboard_entries", "posted_message_id")
    op.drop_column("starboard_entries", "posted_channel_id")

    for entity in _selected_entities(PGFunction, _FUNCTION_NAMES):
        op.execute(entity.to_sql_statement_create())
    for entity in _selected_entities(PGTrigger, _TRIGGER_NAMES):
        op.execute(entity.to_sql_statement_create())


def downgrade() -> None:
    """Revert this revision when the operation is safe."""
    for entity in reversed(_selected_entities(PGTrigger, _TRIGGER_NAMES)):
        op.execute(entity.to_sql_statement_drop())
    for entity in reversed(_selected_entities(PGFunction, _FUNCTION_NAMES)):
        op.execute(entity.to_sql_statement_drop())

    op.add_column("starboard_entries", sa.Column("posted_message_id", sa.BigInteger(), nullable=True))
    op.add_column("starboard_entries", sa.Column("posted_channel_id", sa.BigInteger(), nullable=True))
    op.create_index(
        "starboard_entries_posted_message_key",
        "starboard_entries",
        ["posted_message_id"],
        unique=True,
        postgresql_where=sa.text("posted_message_id IS NOT NULL"),
    )
    op.execute(
        """
        UPDATE starboard_entries e
        SET posted_message_id = p.message_id, posted_channel_id = p.channel_id
        FROM discord_posts p
        WHERE p.resource_kind = 'starboard_entry'
          AND p.suppressed_at IS NULL
          AND p.resource_key = e.starboard_id || ':' || e.origin_message_id
        """
    )
    op.execute("DELETE FROM discord_posts WHERE resource_kind = 'starboard_entry'")
    op.execute("DELETE FROM discord_sync_queue WHERE resource_kind = 'starboard_entry'")

    op.drop_constraint("discord_sync_queue_resource_kind_check", "discord_sync_queue", type_="check")
    op.create_check_constraint(
        "discord_sync_queue_resource_kind_check",
        "discord_sync_queue",
        "resource_kind IN ('build', 'vote_session')",
    )


EntityT = TypeVar("EntityT", bound=ReplaceableEntity)


def _selected_entities(entity_type: type[EntityT], names: set[str]) -> list[EntityT]:
    return [
        entity
        for entity in alembic_util_entities()
        if isinstance(entity, entity_type) and entity.signature.partition("(")[0] in names
    ]
