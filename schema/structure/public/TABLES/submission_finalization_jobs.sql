CREATE TABLE public.submission_finalization_jobs (
    id uuid NOT NULL,
    draft_id uuid NOT NULL,
    draft_revision integer NOT NULL,
    payload jsonb,
    payload_sha256 text,
    status text NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    available_at timestamp with time zone DEFAULT now() NOT NULL,
    claimed_at timestamp with time zone,
    claim_token uuid,
    claim_expires_at timestamp with time zone,
    completed_at timestamp with time zone,
    attention_at timestamp with time zone,
    dead_at timestamp with time zone,
    last_error text,
    attention_issues jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    attempt_number integer DEFAULT 1 NOT NULL,
    requested_by_account_id integer,
    CONSTRAINT submission_finalization_jobs_attempt_number_positive CHECK ((attempt_number > 0)),
    CONSTRAINT submission_finalization_jobs_attempts_nonnegative CHECK ((attempts >= 0)),
    CONSTRAINT submission_finalization_jobs_legacy_sponsor_forbidden CHECK (((payload IS NULL) OR (NOT (((payload ->> 'payload_schema'::text) = '2'::text) AND ((payload ->> 'sponsor_attribution'::text) = 'true'::text))))),
    CONSTRAINT submission_finalization_jobs_payload_sha256_check CHECK (((payload_sha256 IS NULL) OR (payload_sha256 ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT submission_finalization_jobs_revision_nonnegative CHECK ((draft_revision >= 0)),
    CONSTRAINT submission_finalization_jobs_state_shape CHECK ((((status = 'pending'::text) AND (payload IS NOT NULL) AND (payload_sha256 IS NOT NULL) AND (claimed_at IS NULL) AND (claim_token IS NULL) AND (claim_expires_at IS NULL) AND (completed_at IS NULL) AND (attention_at IS NULL) AND (dead_at IS NULL) AND (jsonb_array_length(attention_issues) = 0)) OR ((status = 'claimed'::text) AND (payload IS NOT NULL) AND (payload_sha256 IS NOT NULL) AND (claimed_at IS NOT NULL) AND (claim_token IS NOT NULL) AND (claim_expires_at IS NOT NULL) AND (completed_at IS NULL) AND (attention_at IS NULL) AND (dead_at IS NULL) AND (jsonb_array_length(attention_issues) = 0)) OR ((status = 'needs_attention'::text) AND (claimed_at IS NULL) AND (claim_token IS NULL) AND (claim_expires_at IS NULL) AND (completed_at IS NULL) AND (attention_at IS NOT NULL) AND (dead_at IS NULL) AND (jsonb_array_length(attention_issues) > 0)) OR ((status = 'completed'::text) AND (payload IS NOT NULL) AND (payload_sha256 IS NOT NULL) AND (claimed_at IS NULL) AND (claim_token IS NULL) AND (claim_expires_at IS NULL) AND (completed_at IS NOT NULL) AND (attention_at IS NULL) AND (dead_at IS NULL) AND (jsonb_array_length(attention_issues) = 0)) OR ((status = 'dead'::text) AND (payload IS NOT NULL) AND (payload_sha256 IS NOT NULL) AND (claimed_at IS NULL) AND (claim_token IS NULL) AND (claim_expires_at IS NULL) AND (completed_at IS NULL) AND (attention_at IS NULL) AND (dead_at IS NOT NULL) AND (jsonb_array_length(attention_issues) > 0)))),
    CONSTRAINT submission_finalization_jobs_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'claimed'::text, 'needs_attention'::text, 'completed'::text, 'dead'::text])))
);

COMMENT ON TABLE public.submission_finalization_jobs IS 'A finalization attempt retained with its current worker claim and retry count.';

ALTER TABLE ONLY public.submission_finalization_jobs
    ADD CONSTRAINT submission_finalization_jobs_draft_attempt_key UNIQUE (draft_id, attempt_number);

ALTER TABLE ONLY public.submission_finalization_jobs
    ADD CONSTRAINT submission_finalization_jobs_pkey PRIMARY KEY (id);

CREATE INDEX submission_finalization_jobs_actor_idx ON public.submission_finalization_jobs USING btree (requested_by_account_id) WHERE (requested_by_account_id IS NOT NULL);

CREATE INDEX submission_finalization_jobs_attention_idx ON public.submission_finalization_jobs USING btree (attention_at) WHERE (status = 'needs_attention'::text);

CREATE UNIQUE INDEX submission_finalization_jobs_one_active_attempt ON public.submission_finalization_jobs USING btree (draft_id) WHERE (status = ANY (ARRAY['pending'::text, 'claimed'::text]));

CREATE INDEX submission_finalization_jobs_ready_idx ON public.submission_finalization_jobs USING btree (available_at) WHERE (status = ANY (ARRAY['pending'::text, 'claimed'::text]));

ALTER TABLE ONLY public.submission_finalization_jobs
    ADD CONSTRAINT submission_finalization_jobs_draft_id_fkey FOREIGN KEY (draft_id) REFERENCES public.submission_drafts(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.submission_finalization_jobs
    ADD CONSTRAINT submission_finalization_jobs_requested_by_account_id_fkey FOREIGN KEY (requested_by_account_id) REFERENCES public.accounts(id) ON DELETE RESTRICT;

