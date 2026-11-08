CREATE TABLE public.record_computation_runs (
    id bigint NOT NULL,
    rules_revision_id bigint NOT NULL,
    build_kind text NOT NULL,
    version_id smallint,
    status text DEFAULT 'running'::text NOT NULL,
    is_active boolean DEFAULT false NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    error text,
    CONSTRAINT record_computation_runs_build_kind_check CHECK ((build_kind = ANY (ARRAY['door'::text, 'entrance'::text, 'extender'::text, 'utility'::text]))),
    CONSTRAINT record_computation_runs_status_check CHECK ((status = ANY (ARRAY['running'::text, 'completed'::text, 'failed'::text])))
);

COMMENT ON TABLE public.record_computation_runs IS 'A computation attempt for one build kind and version, including its publication state.';

ALTER TABLE ONLY public.record_computation_runs
    ADD CONSTRAINT record_computation_runs_pkey PRIMARY KEY (id);

CREATE UNIQUE INDEX record_computation_runs_one_active_idx ON public.record_computation_runs USING btree (build_kind, version_id) NULLS NOT DISTINCT WHERE is_active;

CREATE INDEX record_computation_runs_started_idx ON public.record_computation_runs USING btree (started_at);

CREATE TRIGGER record_computation_runs_enqueue_search AFTER INSERT OR DELETE OR UPDATE OF is_active ON public.record_computation_runs FOR EACH ROW EXECUTE FUNCTION public.enqueue_computed_record_search_projection();

ALTER TABLE ONLY public.record_computation_runs
    ADD CONSTRAINT record_computation_runs_rules_revision_id_fkey FOREIGN KEY (rules_revision_id) REFERENCES public.rules_revisions(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.record_computation_runs
    ADD CONSTRAINT record_computation_runs_version_id_fkey FOREIGN KEY (version_id) REFERENCES public.versions(id) ON DELETE RESTRICT;

