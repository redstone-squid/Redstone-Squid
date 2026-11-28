CREATE TABLE public.creator_alias_claims (
    id integer NOT NULL,
    alias_id integer NOT NULL,
    account_id integer NOT NULL,
    status text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    resolved_at timestamp with time zone,
    resolved_by_account_id integer,
    selected_credits jsonb DEFAULT '{}'::jsonb NOT NULL,
    approved_credit_ids jsonb DEFAULT '[]'::jsonb NOT NULL,
    CONSTRAINT creator_alias_claims_resolution_complete CHECK (((status = 'pending'::text) = (resolved_at IS NULL))),
    CONSTRAINT creator_alias_claims_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'approved'::text, 'rejected'::text])))
);

COMMENT ON TABLE public.creator_alias_claims IS 'An account''s request to be credited under a creator alias.';

COMMENT ON COLUMN public.creator_alias_claims.selected_credits IS 'Credit IDs selected at filing and the effective owners staff will review.';

ALTER TABLE ONLY public.creator_alias_claims ALTER COLUMN id SET DEFAULT nextval('public.creator_alias_claims_id_seq'::regclass);

ALTER TABLE ONLY public.creator_alias_claims
    ADD CONSTRAINT creator_alias_claims_pkey PRIMARY KEY (id);

CREATE INDEX creator_alias_claims_account_idx ON public.creator_alias_claims USING btree (account_id);

CREATE INDEX creator_alias_claims_resolved_by_idx ON public.creator_alias_claims USING btree (resolved_by_account_id);

ALTER TABLE ONLY public.creator_alias_claims
    ADD CONSTRAINT creator_alias_claims_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.creator_alias_claims
    ADD CONSTRAINT creator_alias_claims_alias_id_fkey FOREIGN KEY (alias_id) REFERENCES public.creator_aliases(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.creator_alias_claims
    ADD CONSTRAINT creator_alias_claims_resolved_by_account_id_fkey FOREIGN KEY (resolved_by_account_id) REFERENCES public.accounts(id) ON DELETE SET NULL;

