CREATE TABLE public.api_keys (
    id bigint NOT NULL,
    key_id text NOT NULL,
    secret_hash bytea NOT NULL,
    label text NOT NULL,
    scopes text[] DEFAULT '{}'::text[] NOT NULL,
    owner_account_id integer,
    created_by_account_id integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone,
    revoked_at timestamp with time zone,
    last_used_at timestamp with time zone,
    last_used_ip inet
);

COMMENT ON TABLE public.api_keys IS 'A revocable high-entropy credential used by an API service client.';

COMMENT ON COLUMN public.api_keys.id IS 'Internal identifier unrelated to the public token key ID.';

COMMENT ON COLUMN public.api_keys.key_id IS 'Public lookup portion of the API token.';

COMMENT ON COLUMN public.api_keys.secret_hash IS 'HMAC-SHA256 digest of the unrecoverable token secret.

Keyed by a deployment pepper that never reaches this database; see
`docs/credential-hashing.md`.';

COMMENT ON COLUMN public.api_keys.label IS 'Human-readable description of the credential''s owner or purpose.';

COMMENT ON COLUMN public.api_keys.scopes IS 'Capabilities granted to this credential.';

COMMENT ON COLUMN public.api_keys.owner_account_id IS 'Optional account responsible for the credential.';

COMMENT ON COLUMN public.api_keys.created_by_account_id IS 'Account that created the credential, when known.';

COMMENT ON COLUMN public.api_keys.created_at IS 'When the credential was created.';

COMMENT ON COLUMN public.api_keys.expires_at IS 'Optional instant after which authentication is rejected.';

COMMENT ON COLUMN public.api_keys.revoked_at IS 'When the credential was revoked, or ``None`` while active.';

COMMENT ON COLUMN public.api_keys.last_used_at IS 'Most recent throttled usage timestamp.';

COMMENT ON COLUMN public.api_keys.last_used_ip IS 'IP address associated with the most recent recorded use.';

ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT api_keys_pkey PRIMARY KEY (id);

CREATE INDEX api_keys_active ON public.api_keys USING btree (key_id) WHERE (revoked_at IS NULL);

CREATE INDEX api_keys_created_by_idx ON public.api_keys USING btree (created_by_account_id);

CREATE UNIQUE INDEX api_keys_key_id_key ON public.api_keys USING btree (key_id);

CREATE INDEX api_keys_owner_idx ON public.api_keys USING btree (owner_account_id);

ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT api_keys_created_by_account_id_fkey FOREIGN KEY (created_by_account_id) REFERENCES public.accounts(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT api_keys_owner_account_id_fkey FOREIGN KEY (owner_account_id) REFERENCES public.accounts(id) ON DELETE SET NULL;

