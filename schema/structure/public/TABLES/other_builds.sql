CREATE TABLE public.other_builds (
    build_id bigint NOT NULL
);

COMMENT ON TABLE public.other_builds IS 'A build which does not fit one of the structured catalogue categories.';

ALTER TABLE ONLY public.other_builds
    ADD CONSTRAINT other_builds_pkey PRIMARY KEY (build_id);

CREATE TRIGGER other_builds_capture_edit BEFORE INSERT OR DELETE OR UPDATE ON public.other_builds FOR EACH ROW EXECUTE FUNCTION public.capture_build_edit_trigger();

CREATE CONSTRAINT TRIGGER other_builds_category_subtype_check AFTER INSERT OR DELETE OR UPDATE OF build_id ON public.other_builds DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_build_category_subtype();

CREATE TRIGGER other_builds_reject_truncate BEFORE TRUNCATE ON public.other_builds FOR EACH STATEMENT EXECUTE FUNCTION public.capture_build_edit_trigger();

ALTER TABLE ONLY public.other_builds
    ADD CONSTRAINT other_builds_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON DELETE CASCADE;

