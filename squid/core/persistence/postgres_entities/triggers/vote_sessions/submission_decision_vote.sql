CREATE TRIGGER submission_decision_vote AFTER UPDATE OF result ON public.vote_sessions FOR EACH ROW EXECUTE FUNCTION public.record_submission_decision();
