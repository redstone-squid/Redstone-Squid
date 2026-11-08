CREATE TABLE public.submission_drafts (
    id uuid NOT NULL,
    owner_account_id integer NOT NULL,
    schema_id text NOT NULL,
    schema_revision integer NOT NULL,
    category text NOT NULL,
    revision integer DEFAULT 0 NOT NULL,
    status text DEFAULT 'editing'::text NOT NULL,
    answers jsonb NOT NULL,
    origin text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    source_installation_id uuid,
    preparation_issues jsonb DEFAULT '[]'::jsonb NOT NULL,
    preparation_retry_at timestamp with time zone,
    inferred boolean DEFAULT false NOT NULL,
    submission_actor_account_id integer,
    source_issues jsonb DEFAULT '[]'::jsonb NOT NULL,
    source_files jsonb DEFAULT '[]'::jsonb NOT NULL,
    source_messages jsonb DEFAULT '[]'::jsonb NOT NULL,
    inference_run_id uuid,
    CONSTRAINT submission_drafts_expiry_after_creation CHECK ((expires_at > created_at)),
    CONSTRAINT submission_drafts_inference_origin_check CHECK (((NOT inferred) OR (origin = 'discord'::text))),
    CONSTRAINT submission_drafts_installation_requires_paper CHECK (((source_installation_id IS NULL) OR (origin = 'paper'::text))),
    CONSTRAINT submission_drafts_origin_check CHECK ((origin = ANY (ARRAY['discord'::text, 'web'::text, 'cli'::text, 'paper'::text, 'fabric'::text]))),
    CONSTRAINT submission_drafts_preparation_issues_array CHECK ((jsonb_typeof(preparation_issues) = 'array'::text)),
    CONSTRAINT submission_drafts_revision_nonnegative CHECK ((revision >= 0)),
    CONSTRAINT submission_drafts_schema_revision_positive CHECK ((schema_revision > 0)),
    CONSTRAINT submission_drafts_status_check CHECK ((status = ANY (ARRAY['editing'::text, 'processing'::text, 'needs_attention'::text, 'submitted'::text, 'expired'::text])))
);

COMMENT ON TABLE public.submission_drafts IS 'The compact current state of an account-owned submission draft.';

COMMENT ON COLUMN public.submission_drafts.source_installation_id IS 'Server-derived Paper installation retained independently of credential generations.';

ALTER TABLE ONLY public.submission_drafts
    ADD CONSTRAINT submission_drafts_pkey PRIMARY KEY (id);

CREATE INDEX ix_submission_drafts_inference_run_id ON public.submission_drafts USING btree (inference_run_id);

CREATE INDEX submission_drafts_actor_idx ON public.submission_drafts USING btree (submission_actor_account_id) WHERE (submission_actor_account_id IS NOT NULL);

CREATE INDEX submission_drafts_expiry_idx ON public.submission_drafts USING btree (expires_at) WHERE (status = ANY (ARRAY['editing'::text, 'processing'::text, 'needs_attention'::text]));

CREATE INDEX submission_drafts_inferred_expiry_idx ON public.submission_drafts USING btree (expires_at) WHERE inferred;

CREATE INDEX submission_drafts_owner_updated_idx ON public.submission_drafts USING btree (owner_account_id, updated_at);

CREATE INDEX submission_drafts_preparation_ready_idx ON public.submission_drafts USING btree (preparation_retry_at) WHERE (preparation_retry_at IS NOT NULL);

CREATE INDEX submission_drafts_source_installation_idx ON public.submission_drafts USING btree (source_installation_id) WHERE (source_installation_id IS NOT NULL);

CREATE TRIGGER submission_drafts_enqueue_status AFTER INSERT OR DELETE OR UPDATE ON public.submission_drafts FOR EACH ROW EXECUTE FUNCTION public.enqueue_submission_status();

ALTER TABLE ONLY public.submission_drafts
    ADD CONSTRAINT submission_drafts_owner_account_id_fkey FOREIGN KEY (owner_account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.submission_drafts
    ADD CONSTRAINT submission_drafts_submission_actor_account_id_fkey FOREIGN KEY (submission_actor_account_id) REFERENCES public.accounts(id) ON DELETE RESTRICT;

