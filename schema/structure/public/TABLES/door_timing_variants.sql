CREATE TABLE public.door_timing_variants (
    id bigint NOT NULL,
    build_id bigint NOT NULL,
    label text DEFAULT 'default'::text NOT NULL,
    opening_time bigint,
    visible_opening_time bigint,
    closing_time bigint,
    visible_closing_time bigint,
    opening_reset_time bigint,
    closing_reset_time bigint
);

COMMENT ON TABLE public.door_timing_variants IS 'A measured door timing variant used for lexicographic fastest records.';

ALTER TABLE ONLY public.door_timing_variants
    ADD CONSTRAINT door_timing_variants_build_label_key UNIQUE (build_id, label);

ALTER TABLE ONLY public.door_timing_variants
    ADD CONSTRAINT door_timing_variants_pkey PRIMARY KEY (id);

CREATE TRIGGER door_timing_variants_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.door_timing_variants FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();

CREATE TRIGGER door_timing_variants_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.door_timing_variants FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();

ALTER TABLE ONLY public.door_timing_variants
    ADD CONSTRAINT door_timing_variants_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.doors(build_id) ON DELETE CASCADE;

