CREATE TABLE public.submission_finalization_results (
    job_id uuid NOT NULL,
    build_id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT submission_finalization_results_build_id_positive CHECK ((build_id > 0))
);

COMMENT ON TABLE public.submission_finalization_results IS 'Immutable build identity retained after successful finalization.';

ALTER TABLE ONLY public.submission_finalization_results
    ADD CONSTRAINT submission_finalization_results_pkey PRIMARY KEY (job_id);

CREATE INDEX submission_finalization_results_build_idx ON public.submission_finalization_results USING btree (build_id);

ALTER TABLE ONLY public.submission_finalization_results
    ADD CONSTRAINT submission_finalization_results_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.submission_finalization_jobs(id) ON DELETE CASCADE;

