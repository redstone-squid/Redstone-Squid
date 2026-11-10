CREATE TRIGGER permission_grants_bump_epoch AFTER INSERT OR DELETE OR UPDATE ON public.permission_grants FOR EACH STATEMENT EXECUTE FUNCTION public.bump_permission_epoch();
