CREATE CONSTRAINT TRIGGER permission_role_includes_acyclic AFTER INSERT OR UPDATE ON public.permission_role_includes FOR EACH ROW EXECUTE FUNCTION public.check_permission_role_include_cycle();
