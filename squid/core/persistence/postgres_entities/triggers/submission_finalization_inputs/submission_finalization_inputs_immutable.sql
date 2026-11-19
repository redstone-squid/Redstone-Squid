CREATE TRIGGER submission_finalization_inputs_immutable
    BEFORE UPDATE ON public.submission_finalization_inputs
    FOR EACH ROW
    EXECUTE FUNCTION public.reject_submission_finalization_input_update();
