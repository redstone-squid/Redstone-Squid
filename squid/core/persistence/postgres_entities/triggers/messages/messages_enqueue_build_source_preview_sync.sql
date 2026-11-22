CREATE TRIGGER messages_enqueue_build_source_preview_sync
    AFTER UPDATE OF content, deleted_at, channel_id, guild_id ON public.messages
    FOR EACH ROW
    WHEN (OLD.content IS DISTINCT FROM NEW.content
        OR OLD.deleted_at IS DISTINCT FROM NEW.deleted_at
        OR OLD.channel_id IS DISTINCT FROM NEW.channel_id
        OR OLD.guild_id IS DISTINCT FROM NEW.guild_id)
    EXECUTE FUNCTION public.enqueue_build_source_preview_sync();
