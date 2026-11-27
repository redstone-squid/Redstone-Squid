CREATE TABLE public.accounts (
    id integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    privacy_notice_version text,
    privacy_notice_acknowledged_at timestamp with time zone,
    public_account_id uuid DEFAULT gen_random_uuid() NOT NULL,
    revision bigint DEFAULT 1 NOT NULL,
    discord_build_indexing_enabled boolean DEFAULT false NOT NULL,
    CONSTRAINT accounts_privacy_notice_receipt_complete CHECK (((privacy_notice_version IS NULL) = (privacy_notice_acknowledged_at IS NULL))),
    CONSTRAINT accounts_revision_positive CHECK ((revision > 0))
);

COMMENT ON TABLE public.accounts IS 'An internal caller independent of every external identity provider.';

COMMENT ON COLUMN public.accounts.id IS 'Internal account identifier; assigned when the account is first saved.';

ALTER TABLE ONLY public.accounts ALTER COLUMN id SET DEFAULT nextval('public.accounts_id_seq'::regclass);

ALTER TABLE ONLY public.accounts
    ADD CONSTRAINT accounts_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.accounts
    ADD CONSTRAINT accounts_public_account_id_key UNIQUE (public_account_id);

