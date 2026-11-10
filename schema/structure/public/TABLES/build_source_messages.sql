CREATE TABLE public.build_source_messages (
    build_id bigint NOT NULL,
    message_id bigint NOT NULL,
    "position" smallint NOT NULL
);

COMMENT ON TABLE public.build_source_messages IS 'Association table between builds and the Discord messages they came from.

Many-to-many in both directions: one submission can span a body message plus
follow-up images, and one build-log message can yield several builds at once.
The message side is RESTRICT because a message row is a retained fact that
outlives the builds referring to it; deleting a build only drops the link.';

COMMENT ON COLUMN public.build_source_messages."position" IS 'Submission order, so the first message stays identifiable as the request itself.';

ALTER TABLE ONLY public.build_source_messages
    ADD CONSTRAINT build_source_messages_pkey PRIMARY KEY (build_id, message_id);

CREATE TRIGGER build_source_messages_capture_edit BEFORE INSERT OR DELETE OR UPDATE ON public.build_source_messages FOR EACH ROW EXECUTE FUNCTION public.capture_build_edit_trigger();

CREATE TRIGGER build_source_messages_reject_truncate BEFORE TRUNCATE ON public.build_source_messages FOR EACH STATEMENT EXECUTE FUNCTION public.capture_build_edit_trigger();

ALTER TABLE ONLY public.build_source_messages
    ADD CONSTRAINT build_source_messages_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.build_source_messages
    ADD CONSTRAINT build_source_messages_message_id_fkey FOREIGN KEY (message_id) REFERENCES public.messages(id) ON DELETE RESTRICT;

