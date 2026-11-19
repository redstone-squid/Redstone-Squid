CREATE TRIGGER starboard_origin_messages_enqueue_discord_sync AFTER UPDATE OF deleted_at ON public.starboard_origin_messages FOR EACH ROW EXECUTE FUNCTION public.enqueue_starboard_sync();
