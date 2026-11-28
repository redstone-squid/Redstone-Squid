CREATE TABLE public.submission_draft_editors (
    draft_id uuid NOT NULL,
    account_id integer NOT NULL,
    added_at timestamp with time zone DEFAULT now() NOT NULL
);

COMMENT ON TABLE public.submission_draft_editors IS 'An account the draft owner invited to edit answers; the owner itself is on the draft.';

ALTER TABLE ONLY public.submission_draft_editors
    ADD CONSTRAINT submission_draft_editors_pkey PRIMARY KEY (draft_id, account_id);

CREATE INDEX submission_draft_editors_account_idx ON public.submission_draft_editors USING btree (account_id, draft_id);

ALTER TABLE ONLY public.submission_draft_editors
    ADD CONSTRAINT submission_draft_editors_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.submission_draft_editors
    ADD CONSTRAINT submission_draft_editors_draft_id_fkey FOREIGN KEY (draft_id) REFERENCES public.submission_drafts(id) ON DELETE CASCADE;

