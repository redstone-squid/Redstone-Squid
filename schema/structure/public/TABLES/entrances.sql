CREATE TABLE public.entrances (
    build_id bigint NOT NULL
);

COMMENT ON TABLE public.entrances IS 'An entrance build.';

ALTER TABLE ONLY public.entrances
    ADD CONSTRAINT entrances_pkey PRIMARY KEY (build_id);

CREATE CONSTRAINT TRIGGER entrances_category_subtype_check AFTER INSERT OR DELETE OR UPDATE OF build_id ON public.entrances DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_build_category_subtype();

ALTER TABLE ONLY public.entrances
    ADD CONSTRAINT entrances_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON DELETE CASCADE;

