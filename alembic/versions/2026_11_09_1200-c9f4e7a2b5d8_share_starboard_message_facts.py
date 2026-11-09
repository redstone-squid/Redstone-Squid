"""Use canonical messages for starboard origin identity and deletion state."""

import sqlalchemy as sa
from alembic_utils.pg_function import PGFunction

from alembic import op

revision: str = "c9f4e7a2b5d8"
down_revision: str | None = "b8e3d6f9a2c4"
branch_labels: str | None = None
depends_on: str | None = None

_FUNCTION = "CREATE FUNCTION public.enqueue_starboard_sync() RETURNS trigger\n    LANGUAGE plpgsql\n    AS $$\nDECLARE\n    target_key text;\nBEGIN\n    IF TG_TABLE_NAME = 'starboards' THEN\n        -- A configuration change restyles or re-thresholds every entry on the board.\n        INSERT INTO public.discord_sync_queue\n            (resource_kind, source_key, action, generation, enqueued_at, available_at,\n             claimed_at, claim_token, dead_at, attempts, last_error)\n        SELECT\n            'starboard_entry', e.starboard_id || ':' || e.origin_message_id, 'refresh',\n            nextval('public.discord_sync_generation_seq'), now(), now(), NULL, NULL, NULL, 0, NULL\n        FROM public.starboard_entries e\n        WHERE e.starboard_id = CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END\n        ON CONFLICT (resource_kind, source_key) DO UPDATE\n        SET generation = EXCLUDED.generation, enqueued_at = EXCLUDED.enqueued_at,\n            available_at = EXCLUDED.available_at,\n            claimed_at = NULL, claim_token = NULL, dead_at = NULL, attempts = 0, last_error = NULL;\n        RETURN NULL;\n    END IF;\n\n    IF TG_TABLE_NAME = 'messages' THEN\n        target_key := NULL;\n        INSERT INTO public.discord_sync_queue\n            (resource_kind, source_key, action, generation, enqueued_at, available_at,\n             claimed_at, claim_token, dead_at, attempts, last_error)\n        SELECT\n            'starboard_entry', e.starboard_id || ':' || e.origin_message_id, 'refresh',\n            nextval('public.discord_sync_generation_seq'), now(), now(), NULL, NULL, NULL, 0, NULL\n        FROM public.starboard_entries e\n        WHERE e.origin_message_id = CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END\n        ON CONFLICT (resource_kind, source_key) DO UPDATE\n        SET generation = EXCLUDED.generation, enqueued_at = EXCLUDED.enqueued_at,\n            available_at = EXCLUDED.available_at,\n            claimed_at = NULL, claim_token = NULL, dead_at = NULL, attempts = 0, last_error = NULL;\n        RETURN NULL;\n    END IF;\n\n    IF TG_OP = 'DELETE' THEN\n        target_key := OLD.starboard_id || ':' || OLD.origin_message_id;\n    ELSE\n        target_key := NEW.starboard_id || ':' || NEW.origin_message_id;\n    END IF;\n    INSERT INTO public.discord_sync_queue\n        (resource_kind, source_key, action, generation, enqueued_at, available_at,\n         claimed_at, claim_token, dead_at, attempts, last_error)\n    VALUES (\n        'starboard_entry', target_key, CASE WHEN TG_OP = 'DELETE' THEN 'delete' ELSE 'refresh' END,\n        nextval('public.discord_sync_generation_seq'), now(), now(), NULL, NULL, NULL, 0, NULL\n    )\n    ON CONFLICT (resource_kind, source_key) DO UPDATE\n    SET action = EXCLUDED.action, generation = EXCLUDED.generation, enqueued_at = EXCLUDED.enqueued_at,\n        available_at = EXCLUDED.available_at,\n        claimed_at = NULL, claim_token = NULL, dead_at = NULL, attempts = 0, last_error = NULL;\n    RETURN NULL;\nEND;\n$$;"


def upgrade() -> None:
    op.execute("DROP TRIGGER starboard_origin_messages_enqueue_discord_sync ON starboard_origin_messages")
    # Keep captured content and either source's deletion notice when the rows already overlap.
    op.execute("""
        INSERT INTO messages (id, guild_id, channel_id, author_id, created_at, observed_at, deleted_at)
        SELECT id, guild_id, channel_id, author_id, posted_at, seen_at, deleted_at
        FROM starboard_origin_messages
        ON CONFLICT (id) DO UPDATE SET
            guild_id = COALESCE(messages.guild_id, EXCLUDED.guild_id),
            channel_id = COALESCE(messages.channel_id, EXCLUDED.channel_id),
            created_at = COALESCE(messages.created_at, EXCLUDED.created_at),
            deleted_at = LEAST(messages.deleted_at, EXCLUDED.deleted_at)
    """)
    op.drop_index("starboard_origin_messages_guild_idx", table_name="starboard_origin_messages")
    for column in ("guild_id", "channel_id", "author_id", "posted_at", "deleted_at"):
        op.drop_column("starboard_origin_messages", column)
    op.create_foreign_key(
        "starboard_origin_messages_id_fkey",
        "starboard_origin_messages",
        "messages",
        ["id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.replace_entity(PGFunction.from_sql(_FUNCTION))
    op.execute("""
        CREATE TRIGGER messages_enqueue_starboard_sync AFTER UPDATE OF deleted_at ON public.messages
        FOR EACH ROW WHEN (OLD.deleted_at IS DISTINCT FROM NEW.deleted_at)
        EXECUTE FUNCTION public.enqueue_starboard_sync()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER messages_enqueue_starboard_sync ON messages")
    op.drop_constraint("starboard_origin_messages_id_fkey", "starboard_origin_messages", type_="foreignkey")
    for column in ("guild_id", "channel_id", "author_id"):
        op.add_column("starboard_origin_messages", sa.Column(column, sa.BigInteger(), nullable=True))
    for column in ("posted_at", "deleted_at"):
        op.add_column("starboard_origin_messages", sa.Column(column, sa.DateTime(timezone=True), nullable=True))
    op.execute("""
        UPDATE starboard_origin_messages o SET
            guild_id = m.guild_id, channel_id = m.channel_id, author_id = m.author_id,
            posted_at = m.created_at, deleted_at = m.deleted_at
        FROM messages m WHERE m.id = o.id
    """)
    for column in ("guild_id", "channel_id", "author_id", "posted_at"):
        op.alter_column("starboard_origin_messages", column, nullable=False)
    op.create_foreign_key(
        "starboard_origin_messages_guild_id_fkey",
        "starboard_origin_messages",
        "server_settings",
        ["guild_id"],
        ["server_id"],
        ondelete="CASCADE",
    )
    op.create_index("starboard_origin_messages_guild_idx", "starboard_origin_messages", ["guild_id"])
    op.replace_entity(
        PGFunction.from_sql(
            _FUNCTION.replace("TG_TABLE_NAME = 'messages'", "TG_TABLE_NAME = 'starboard_origin_messages'")
        )
    )
    op.execute("""
        CREATE TRIGGER starboard_origin_messages_enqueue_discord_sync
        AFTER UPDATE OF deleted_at ON public.starboard_origin_messages
        FOR EACH ROW EXECUTE FUNCTION public.enqueue_starboard_sync()
    """)
