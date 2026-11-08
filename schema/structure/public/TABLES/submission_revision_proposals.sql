CREATE TABLE public.submission_revision_proposals (
    id uuid NOT NULL,
    run_id uuid NOT NULL,
    owner_account_id integer NOT NULL,
    requested_by_account_id integer NOT NULL,
    source_message_id bigint NOT NULL,
    facts jsonb NOT NULL,
    build_id integer,
    expected_revision integer,
    before jsonb NOT NULL,
    after jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    approved_by_account_id integer,
    applied_revision integer,
    CONSTRAINT submission_revision_proposals_receipt_check CHECK (((approved_by_account_id IS NULL) = (applied_revision IS NULL))),
    CONSTRAINT submission_revision_proposals_revision_check CHECK (((expected_revision IS NULL) OR (expected_revision >= 0))),
    CONSTRAINT submission_revision_proposals_target_check CHECK (((build_id IS NULL) = (expected_revision IS NULL)))
);

COMMENT ON TABLE public.submission_revision_proposals IS 'A retained inference candidate, review snapshot, and atomic build revision receipt.';

ALTER TABLE ONLY public.submission_revision_proposals
    ADD CONSTRAINT submission_revision_proposals_pkey PRIMARY KEY (id);

CREATE INDEX submission_revision_proposals_actor_idx ON public.submission_revision_proposals USING btree (requested_by_account_id);

CREATE INDEX submission_revision_proposals_approver_idx ON public.submission_revision_proposals USING btree (approved_by_account_id);

CREATE INDEX submission_revision_proposals_build_idx ON public.submission_revision_proposals USING btree (build_id);

CREATE INDEX submission_revision_proposals_expiry_idx ON public.submission_revision_proposals USING btree (expires_at);

CREATE INDEX submission_revision_proposals_owner_idx ON public.submission_revision_proposals USING btree (owner_account_id);

CREATE INDEX submission_revision_proposals_run_idx ON public.submission_revision_proposals USING btree (run_id);

CREATE INDEX submission_revision_proposals_source_idx ON public.submission_revision_proposals USING btree (source_message_id);

ALTER TABLE ONLY public.submission_revision_proposals
    ADD CONSTRAINT submission_revision_proposals_approved_by_account_id_fkey FOREIGN KEY (approved_by_account_id) REFERENCES public.accounts(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.submission_revision_proposals
    ADD CONSTRAINT submission_revision_proposals_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.submission_revision_proposals
    ADD CONSTRAINT submission_revision_proposals_owner_account_id_fkey FOREIGN KEY (owner_account_id) REFERENCES public.accounts(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.submission_revision_proposals
    ADD CONSTRAINT submission_revision_proposals_requested_by_account_id_fkey FOREIGN KEY (requested_by_account_id) REFERENCES public.accounts(id) ON DELETE RESTRICT;

