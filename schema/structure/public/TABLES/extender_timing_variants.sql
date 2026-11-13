CREATE TABLE public.extender_timing_variants (
    id bigint NOT NULL,
    build_id bigint NOT NULL,
    label text DEFAULT 'default'::text NOT NULL,
    retraction_time bigint,
    extension_time bigint,
    retraction_reset_time bigint,
    extension_reset_time bigint
);

COMMENT ON TABLE public.extender_timing_variants IS 'A measured piston-extender timing variant used for fastest records.';

ALTER TABLE ONLY public.extender_timing_variants
    ADD CONSTRAINT extender_timing_variants_build_label_key UNIQUE (build_id, label);

ALTER TABLE ONLY public.extender_timing_variants
    ADD CONSTRAINT extender_timing_variants_pkey PRIMARY KEY (id);

CREATE TRIGGER extender_timing_variants_enqueue_build_change AFTER INSERT OR DELETE OR UPDATE ON public.extender_timing_variants FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_change();

ALTER TABLE ONLY public.extender_timing_variants
    ADD CONSTRAINT extender_timing_variants_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.extenders(build_id) ON DELETE CASCADE;

