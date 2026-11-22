"""Refresh published build previews when their source messages change."""

from alembic import op

revision = "e3af0afeec12"
down_revision = "812a54bc3f09"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("build_source_messages_message_id_idx", "build_source_messages", ["message_id"])
    op.execute("""
CREATE FUNCTION public.enqueue_build_source_preview_sync() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    PERFORM public.upsert_discord_sync_request('build', source.build_id::text, 'refresh')
    FROM public.build_source_messages source
    WHERE source.message_id = NEW.id
    ORDER BY source.build_id;

    PERFORM public.upsert_discord_sync_request('vote_session', review.vote_session_id::text, 'refresh')
    FROM public.build_source_messages source
    JOIN public.build_vote_sessions review ON review.build_id = source.build_id
    WHERE source.message_id = NEW.id
    ORDER BY review.vote_session_id;
    RETURN NULL;
END;
$$;
    """)
    op.execute("""
CREATE TRIGGER messages_enqueue_build_source_preview_sync
    AFTER UPDATE OF content, deleted_at, channel_id, guild_id ON public.messages
    FOR EACH ROW
    WHEN (OLD.content IS DISTINCT FROM NEW.content
        OR OLD.deleted_at IS DISTINCT FROM NEW.deleted_at
        OR OLD.channel_id IS DISTINCT FROM NEW.channel_id
        OR OLD.guild_id IS DISTINCT FROM NEW.guild_id)
    EXECUTE FUNCTION public.enqueue_build_source_preview_sync();
    """)
    # Refresh existing cards that may gain an excerpt after deployment.
    op.execute("""
        SELECT public.upsert_discord_sync_request(post.resource_kind, post.resource_key, 'refresh')
        FROM (
            SELECT DISTINCT resource_kind, resource_key
            FROM public.discord_posts
            WHERE suppressed_at IS NULL AND surface IN ('build_card', 'build_review')
        ) post
        ORDER BY post.resource_kind, post.resource_key
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER messages_enqueue_build_source_preview_sync ON public.messages")
    op.execute("DROP FUNCTION public.enqueue_build_source_preview_sync()")
    op.drop_index("build_source_messages_message_id_idx", table_name="build_source_messages")
