CREATE TABLE public.accounts (
    id integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    consent_version text,
    consented_at timestamp with time zone,
    public_creator_id uuid DEFAULT gen_random_uuid() NOT NULL,
    revision bigint DEFAULT 1 NOT NULL,
    CONSTRAINT accounts_consent_receipt_complete CHECK (((consent_version IS NULL) = (consented_at IS NULL))),
    CONSTRAINT accounts_revision_positive CHECK ((revision > 0))
);

COMMENT ON TABLE public.accounts IS 'An internal caller independent of every external identity provider.';

COMMENT ON COLUMN public.accounts.id IS 'Internal account identifier; assigned when the account is first saved.';

ALTER TABLE ONLY public.accounts ALTER COLUMN id SET DEFAULT nextval('public.accounts_id_seq'::regclass);

ALTER TABLE ONLY public.accounts
    ADD CONSTRAINT accounts_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.accounts
    ADD CONSTRAINT accounts_public_creator_id_key UNIQUE (public_creator_id);

