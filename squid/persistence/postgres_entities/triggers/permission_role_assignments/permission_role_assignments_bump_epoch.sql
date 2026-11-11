CREATE TRIGGER permission_role_assignments_bump_epoch BEFORE INSERT OR DELETE OR UPDATE ON public.permission_role_assignments FOR EACH STATEMENT EXECUTE FUNCTION public.bump_permission_epoch();
