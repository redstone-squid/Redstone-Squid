CREATE TABLE public.record_computation_runs (
    id bigint NOT NULL,
    rules_revision_id bigint NOT NULL,
    build_kind text NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone DEFAULT now() NOT NULL,
    "full" boolean DEFAULT false NOT NULL,
    input_generation bigint NOT NULL,
    CONSTRAINT record_computation_runs_build_kind_check CHECK ((build_kind = ANY (ARRAY['door'::text, 'entrance'::text, 'extender'::text, 'utility'::text])))
);

COMMENT ON TABLE public.record_computation_runs IS 'One published batch of one build kind; results and drift name the run that wrote them.';

ALTER TABLE ONLY public.record_computation_runs
    ADD CONSTRAINT record_computation_runs_pkey PRIMARY KEY (id);

CREATE INDEX record_computation_runs_started_idx ON public.record_computation_runs USING btree (started_at);

ALTER TABLE ONLY public.record_computation_runs
    ADD CONSTRAINT record_computation_runs_rules_revision_id_fkey FOREIGN KEY (rules_revision_id) REFERENCES public.rules_revisions(id) ON DELETE RESTRICT;

