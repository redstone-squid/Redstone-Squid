CREATE TABLE public.submission_draft_access (
    id bigint NOT NULL,
    draft_id uuid NOT NULL,
    account_id integer NOT NULL,
    role text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT submission_draft_access_role_check CHECK ((role = ANY (ARRAY['owner'::text, 'editor'::text])))
);

COMMENT ON TABLE public.submission_draft_access IS 'An account''s role on a draft; creation grants one account the owner role.';

ALTER TABLE ONLY public.submission_draft_access
    ADD CONSTRAINT submission_draft_access_draft_account_key UNIQUE (draft_id, account_id);

ALTER TABLE ONLY public.submission_draft_access
    ADD CONSTRAINT submission_draft_access_pkey PRIMARY KEY (id);

CREATE INDEX submission_draft_access_account_idx ON public.submission_draft_access USING btree (account_id, draft_id);

CREATE UNIQUE INDEX submission_draft_access_one_owner ON public.submission_draft_access USING btree (draft_id) WHERE (role = 'owner'::text);

ALTER TABLE ONLY public.submission_draft_access
    ADD CONSTRAINT submission_draft_access_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.submission_draft_access
    ADD CONSTRAINT submission_draft_access_draft_id_fkey FOREIGN KEY (draft_id) REFERENCES public.submission_drafts(id) ON DELETE CASCADE;

