CREATE TRIGGER starboards_enqueue_discord_sync AFTER UPDATE ON public.starboards FOR EACH ROW EXECUTE FUNCTION public.enqueue_starboard_sync();
