CREATE TABLE public.door_range_runs (
    id bigint NOT NULL,
    query_id bigint NOT NULL,
    generation bigint NOT NULL,
    rules_revision_id bigint NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    result jsonb,
    error text,
    is_active boolean DEFAULT false NOT NULL,
    CONSTRAINT door_range_runs_active_check CHECK (((NOT is_active) OR ((((result -> 'analysis'::text) ->> 'status'::text) = 'exact'::text) IS TRUE))),
    CONSTRAINT door_range_runs_completion_check CHECK ((((result IS NOT NULL) OR (error IS NOT NULL)) = (completed_at IS NOT NULL))),
    CONSTRAINT door_range_runs_generation_check CHECK ((generation > 0))
);

COMMENT ON TABLE public.door_range_runs IS 'A reserved attempt whose complete result can replace one registered query.';

ALTER TABLE ONLY public.door_range_runs
    ADD CONSTRAINT door_range_runs_generation_key UNIQUE (query_id, generation);

ALTER TABLE ONLY public.door_range_runs
    ADD CONSTRAINT door_range_runs_pkey PRIMARY KEY (id);

CREATE UNIQUE INDEX door_range_runs_one_active_idx ON public.door_range_runs USING btree (query_id) WHERE is_active;

ALTER TABLE ONLY public.door_range_runs
    ADD CONSTRAINT door_range_runs_query_id_fkey FOREIGN KEY (query_id) REFERENCES public.door_range_registrations(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.door_range_runs
    ADD CONSTRAINT door_range_runs_rules_revision_id_fkey FOREIGN KEY (rules_revision_id) REFERENCES public.rules_revisions(id) ON DELETE RESTRICT;

