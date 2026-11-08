CREATE TABLE public.submission_draft_changes (
    id bigint NOT NULL,
    draft_id uuid NOT NULL,
    actor_account_id integer NOT NULL,
    base_revision integer NOT NULL,
    resulting_revision integer NOT NULL,
    client_instance_id text NOT NULL,
    idempotency_key text NOT NULL,
    operations jsonb NOT NULL,
    applied_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT submission_draft_changes_base_revision_nonnegative CHECK ((base_revision >= 0)),
    CONSTRAINT submission_draft_changes_has_operations CHECK ((jsonb_array_length(operations) > 0)),
    CONSTRAINT submission_draft_changes_revision_sequence CHECK ((resulting_revision = (base_revision + 1)))
);

COMMENT ON TABLE public.submission_draft_changes IS 'An immutable accepted field-operation batch used for retries and audit.';

ALTER TABLE ONLY public.submission_draft_changes
    ADD CONSTRAINT submission_draft_changes_draft_idempotency_key UNIQUE (draft_id, idempotency_key);

ALTER TABLE ONLY public.submission_draft_changes
    ADD CONSTRAINT submission_draft_changes_draft_revision_key UNIQUE (draft_id, resulting_revision);

ALTER TABLE ONLY public.submission_draft_changes
    ADD CONSTRAINT submission_draft_changes_pkey PRIMARY KEY (id);

CREATE INDEX submission_draft_changes_actor_idx ON public.submission_draft_changes USING btree (actor_account_id, applied_at);

ALTER TABLE ONLY public.submission_draft_changes
    ADD CONSTRAINT submission_draft_changes_actor_account_id_fkey FOREIGN KEY (actor_account_id) REFERENCES public.accounts(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.submission_draft_changes
    ADD CONSTRAINT submission_draft_changes_draft_id_fkey FOREIGN KEY (draft_id) REFERENCES public.submission_drafts(id) ON DELETE CASCADE;

