CREATE TRIGGER build_edit_history_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON public.build_edit_history FOR EACH STATEMENT EXECUTE FUNCTION public.reject_build_history_mutation();
