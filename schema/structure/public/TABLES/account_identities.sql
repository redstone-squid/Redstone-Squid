CREATE TABLE public.account_identities (
    id bigint NOT NULL,
    account_id integer NOT NULL,
    provider text NOT NULL,
    subject text NOT NULL,
    display_name text,
    verified_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    is_public boolean DEFAULT true NOT NULL,
    avatar_key text,
    CONSTRAINT account_identities_provider_check CHECK ((provider = ANY (ARRAY['discord'::text, 'java'::text, 'bedrock'::text]))),
    CONSTRAINT account_identities_subject_check CHECK (((subject = btrim(subject)) AND (subject <> ''::text)))
);

COMMENT ON TABLE public.account_identities IS 'A verified provider subject attached to exactly one account.';

COMMENT ON COLUMN public.account_identities.is_public IS 'Whether this identity appears on the account''s public creator profile.';

COMMENT ON COLUMN public.account_identities.avatar_key IS 'Provider avatar key, such as a Discord avatar hash. Java avatars use the subject UUID.';

ALTER TABLE ONLY public.account_identities
    ADD CONSTRAINT account_identities_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.account_identities
    ADD CONSTRAINT account_identities_provider_subject_key UNIQUE (provider, subject);

CREATE INDEX account_identities_account_provider_idx ON public.account_identities USING btree (account_id, provider);

ALTER TABLE ONLY public.account_identities
    ADD CONSTRAINT account_identities_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;

