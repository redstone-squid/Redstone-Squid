CREATE TRIGGER trg_sync_on_tag_alias
AFTER INSERT ON public.restriction_aliases FOR EACH ROW
EXECUTE FUNCTION sync_new_restriction ();