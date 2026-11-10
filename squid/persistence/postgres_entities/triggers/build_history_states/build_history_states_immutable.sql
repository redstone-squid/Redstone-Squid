CREATE TRIGGER build_history_states_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON public.build_history_states FOR EACH STATEMENT EXECUTE FUNCTION public.reject_build_history_mutation();
