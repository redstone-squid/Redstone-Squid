CREATE TABLE public.record_drift (
    id bigint NOT NULL,
    run_id bigint NOT NULL,
    title_id uuid NOT NULL,
    detected_at timestamp with time zone DEFAULT now() NOT NULL,
    published jsonb NOT NULL,
    computed jsonb NOT NULL
);

COMMENT ON TABLE public.record_drift IS 'A record a backstop recompute changed although no queued change explained it.';

COMMENT ON COLUMN public.record_drift.published IS 'Holders, gaps and members before the backstop; empty for a retired record.';

COMMENT ON COLUMN public.record_drift.computed IS 'The same facts as the backstop computed them; empty when it retired the record.';

ALTER TABLE ONLY public.record_drift
    ADD CONSTRAINT record_drift_pkey PRIMARY KEY (id);

CREATE INDEX record_drift_run_id_idx ON public.record_drift USING btree (run_id);

CREATE INDEX record_drift_title_idx ON public.record_drift USING btree (title_id);

ALTER TABLE ONLY public.record_drift
    ADD CONSTRAINT record_drift_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.record_computation_runs(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.record_drift
    ADD CONSTRAINT record_drift_title_id_fkey FOREIGN KEY (title_id) REFERENCES public.record_titles(public_id) ON DELETE CASCADE;

