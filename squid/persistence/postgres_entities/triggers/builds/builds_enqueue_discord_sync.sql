CREATE TRIGGER builds_enqueue_discord_sync AFTER INSERT OR DELETE ON public.builds FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();
