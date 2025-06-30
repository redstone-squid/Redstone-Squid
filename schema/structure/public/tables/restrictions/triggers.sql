CREATE TRIGGER trg_sync_on_tag
AFTER INSERT ON public.restrictions FOR EACH ROW
EXECUTE FUNCTION sync_new_restriction ();