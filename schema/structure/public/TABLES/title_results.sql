CREATE TABLE public.title_results (
    id bigint NOT NULL,
    run_id bigint NOT NULL,
    definition_id bigint NOT NULL,
    gap_reasons jsonb DEFAULT '{}'::jsonb NOT NULL,
    history_complete boolean DEFAULT true NOT NULL,
    computed_at timestamp with time zone DEFAULT now() NOT NULL,
    door_evidence jsonb,
    title_id uuid NOT NULL,
    fingerprint text NOT NULL,
    last_checked_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT title_results_door_evidence_check CHECK (((door_evidence IS NULL) OR (((jsonb_typeof(door_evidence) = 'object'::text) AND ((door_evidence -> 'schema_version'::text) = '1'::jsonb)) IS TRUE)))
);

COMMENT ON TABLE public.title_results IS 'A live record''s current outcome; a record without competitors has none.';

COMMENT ON COLUMN public.title_results.run_id IS 'The batch that last changed this result.';

COMMENT ON COLUMN public.title_results.fingerprint IS 'A digest of the published contents, to skip republishing unchanged results.';

ALTER TABLE ONLY public.title_results
    ADD CONSTRAINT title_results_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.title_results
    ADD CONSTRAINT title_results_title_key UNIQUE (title_id);

CREATE INDEX title_results_definition_idx ON public.title_results USING btree (definition_id);

CREATE INDEX title_results_run_idx ON public.title_results USING btree (run_id);

CREATE TRIGGER title_results_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.title_results FOR EACH ROW EXECUTE FUNCTION public.enqueue_computed_record_search_projection();

ALTER TABLE ONLY public.title_results
    ADD CONSTRAINT title_results_definition_id_fkey FOREIGN KEY (definition_id) REFERENCES public.title_definitions(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.title_results
    ADD CONSTRAINT title_results_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.record_computation_runs(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.title_results
    ADD CONSTRAINT title_results_title_id_fkey FOREIGN KEY (title_id) REFERENCES public.record_titles(public_id) ON DELETE CASCADE;

