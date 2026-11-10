CREATE TRIGGER build_source_messages_reject_truncate BEFORE TRUNCATE ON public.build_source_messages FOR EACH STATEMENT EXECUTE FUNCTION public.capture_build_edit_trigger();
