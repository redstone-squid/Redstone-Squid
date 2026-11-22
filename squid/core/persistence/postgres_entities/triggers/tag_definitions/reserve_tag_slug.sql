CREATE TRIGGER tag_definitions_reserve_slug
BEFORE INSERT OR UPDATE OF slug ON public.tag_definitions
FOR EACH ROW EXECUTE FUNCTION public.reserve_tag_slug();
