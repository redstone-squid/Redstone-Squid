CREATE TRIGGER permission_roles_bump_epoch BEFORE INSERT OR DELETE OR UPDATE ON public.permission_roles FOR EACH STATEMENT EXECUTE FUNCTION public.bump_permission_epoch();
