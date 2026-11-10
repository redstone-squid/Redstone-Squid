CREATE CONSTRAINT TRIGGER build_edit_finalize AFTER INSERT ON public.build_edit_pending DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.finish_build_edit_trigger();
