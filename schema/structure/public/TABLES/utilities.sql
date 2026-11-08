CREATE TABLE public.utilities (
    build_id bigint NOT NULL
);

COMMENT ON TABLE public.utilities IS 'A utility build.';

ALTER TABLE ONLY public.utilities
    ADD CONSTRAINT utilities_pkey PRIMARY KEY (build_id);

CREATE CONSTRAINT TRIGGER utilities_category_subtype_check AFTER INSERT OR DELETE OR UPDATE OF build_id ON public.utilities DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_build_category_subtype();

ALTER TABLE ONLY public.utilities
    ADD CONSTRAINT utilities_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON DELETE CASCADE;

