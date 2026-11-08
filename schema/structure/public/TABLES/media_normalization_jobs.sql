CREATE TABLE public.media_normalization_jobs (
    upload_id uuid NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    available_at timestamp with time zone DEFAULT now() NOT NULL,
    claimed_at timestamp with time zone,
    claim_token uuid,
    attempts integer DEFAULT 0 NOT NULL,
    completed_at timestamp with time zone,
    dead_at timestamp with time zone,
    discarded_at timestamp with time zone,
    last_error text,
    CONSTRAINT media_normalization_jobs_attempts_nonnegative CHECK ((attempts >= 0)),
    CONSTRAINT media_normalization_jobs_claim_complete CHECK (((claimed_at IS NULL) = (claim_token IS NULL))),
    CONSTRAINT media_normalization_jobs_state_shape CHECK ((((status = 'pending'::text) AND (claimed_at IS NULL) AND (claim_token IS NULL) AND (completed_at IS NULL) AND (dead_at IS NULL) AND (discarded_at IS NULL)) OR ((status = 'claimed'::text) AND (claimed_at IS NOT NULL) AND (claim_token IS NOT NULL) AND (completed_at IS NULL) AND (dead_at IS NULL) AND (discarded_at IS NULL)) OR ((status = 'completed'::text) AND (claimed_at IS NULL) AND (claim_token IS NULL) AND (completed_at IS NOT NULL) AND (dead_at IS NULL) AND (discarded_at IS NULL)) OR ((status = 'dead'::text) AND (claimed_at IS NULL) AND (claim_token IS NULL) AND (completed_at IS NULL) AND (dead_at IS NOT NULL) AND (discarded_at IS NULL)) OR ((status = 'discarded'::text) AND (claimed_at IS NULL) AND (claim_token IS NULL) AND (completed_at IS NULL) AND (dead_at IS NULL) AND (discarded_at IS NOT NULL)))),
    CONSTRAINT media_normalization_jobs_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'claimed'::text, 'completed'::text, 'dead'::text, 'discarded'::text])))
);

COMMENT ON TABLE public.media_normalization_jobs IS 'A normalization request retained after completion, failure, or discard for cleanup and retries.';

ALTER TABLE ONLY public.media_normalization_jobs
    ADD CONSTRAINT media_normalization_jobs_pkey PRIMARY KEY (upload_id);

CREATE INDEX media_normalization_jobs_ready_idx ON public.media_normalization_jobs USING btree (available_at) WHERE (status = ANY (ARRAY['pending'::text, 'claimed'::text]));

CREATE INDEX media_normalization_jobs_terminal_idx ON public.media_normalization_jobs USING btree (upload_id) WHERE (status = ANY (ARRAY['completed'::text, 'dead'::text, 'discarded'::text]));

ALTER TABLE ONLY public.media_normalization_jobs
    ADD CONSTRAINT media_normalization_jobs_upload_id_fkey FOREIGN KEY (upload_id) REFERENCES public.media_uploads(id) ON DELETE CASCADE;

