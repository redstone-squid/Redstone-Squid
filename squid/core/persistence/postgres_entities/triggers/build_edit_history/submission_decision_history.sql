CREATE TRIGGER submission_decision_history AFTER INSERT ON public.build_edit_history FOR EACH ROW EXECUTE FUNCTION public.record_submission_decision();
