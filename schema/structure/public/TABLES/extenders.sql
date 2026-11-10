CREATE TABLE public.extenders (
    build_id bigint NOT NULL,
    orientation text,
    extension_length integer,
    extender_type text
);

COMMENT ON TABLE public.extenders IS 'An extender build.';

ALTER TABLE ONLY public.extenders
    ADD CONSTRAINT extenders_pkey PRIMARY KEY (build_id);

CREATE TRIGGER extenders_capture_edit BEFORE INSERT OR DELETE OR UPDATE ON public.extenders FOR EACH ROW EXECUTE FUNCTION public.capture_build_edit_trigger();

CREATE CONSTRAINT TRIGGER extenders_category_subtype_check AFTER INSERT OR DELETE OR UPDATE OF build_id ON public.extenders DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_build_category_subtype();

CREATE TRIGGER extenders_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.extenders FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();

CREATE TRIGGER extenders_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.extenders FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();

CREATE TRIGGER extenders_reject_truncate BEFORE TRUNCATE ON public.extenders FOR EACH STATEMENT EXECUTE FUNCTION public.capture_build_edit_trigger();

ALTER TABLE ONLY public.extenders
    ADD CONSTRAINT extenders_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON DELETE CASCADE;

