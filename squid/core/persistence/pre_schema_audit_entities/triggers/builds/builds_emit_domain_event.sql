CREATE TRIGGER builds_emit_domain_event AFTER INSERT OR UPDATE OF submission_status ON public.builds FOR EACH ROW EXECUTE FUNCTION public.emit_domain_event();
