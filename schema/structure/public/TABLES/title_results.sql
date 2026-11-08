CREATE TABLE public.title_results (
    id bigint NOT NULL,
    run_id bigint NOT NULL,
    definition_id bigint NOT NULL,
    status text NOT NULL,
    gap_reasons jsonb DEFAULT '{}'::jsonb NOT NULL,
    provisional_build_id bigint,
    history_complete boolean DEFAULT true NOT NULL,
    computed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT title_results_status_check CHECK ((status = ANY (ARRAY['resolved'::text, 'unresolved'::text, 'no_candidate'::text])))
);

COMMENT ON TABLE public.title_results IS 'The outcome for one definition in a computation run.';

ALTER TABLE ONLY public.title_results
    ADD CONSTRAINT title_results_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.title_results
    ADD CONSTRAINT title_results_run_definition_key UNIQUE (run_id, definition_id);

CREATE INDEX title_results_definition_idx ON public.title_results USING btree (definition_id);

CREATE INDEX title_results_provisional_build_idx ON public.title_results USING btree (provisional_build_id);

CREATE TRIGGER title_results_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.title_results FOR EACH ROW EXECUTE FUNCTION public.enqueue_computed_record_search_projection();

ALTER TABLE ONLY public.title_results
    ADD CONSTRAINT title_results_definition_id_fkey FOREIGN KEY (definition_id) REFERENCES public.title_definitions(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.title_results
    ADD CONSTRAINT title_results_provisional_build_id_fkey FOREIGN KEY (provisional_build_id) REFERENCES public.builds(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.title_results
    ADD CONSTRAINT title_results_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.record_computation_runs(id) ON DELETE CASCADE;

