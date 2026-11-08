CREATE TABLE public.minecraft_player_grants (
    id uuid NOT NULL,
    challenge_id uuid NOT NULL,
    token_hash bytea NOT NULL,
    account_id integer NOT NULL,
    java_uuid uuid NOT NULL,
    origin text NOT NULL,
    installation_id uuid,
    installation_credential_version integer,
    issued_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone,
    CONSTRAINT minecraft_player_grants_expiry_after_issue CHECK ((expires_at > issued_at)),
    CONSTRAINT minecraft_player_grants_origin_binding CHECK ((((origin = 'paper'::text) AND (installation_id IS NOT NULL) AND (installation_credential_version IS NOT NULL)) OR ((origin = 'fabric'::text) AND (installation_id IS NULL) AND (installation_credential_version IS NULL)))),
    CONSTRAINT minecraft_player_grants_origin_check CHECK ((origin = ANY (ARRAY['paper'::text, 'fabric'::text]))),
    CONSTRAINT minecraft_player_grants_token_hash_length CHECK ((octet_length(token_hash) = 32))
);

COMMENT ON TABLE public.minecraft_player_grants IS 'A short-lived origin- and identity-bound player bearer grant.';

ALTER TABLE ONLY public.minecraft_player_grants
    ADD CONSTRAINT minecraft_player_grants_challenge_id_key UNIQUE (challenge_id);

ALTER TABLE ONLY public.minecraft_player_grants
    ADD CONSTRAINT minecraft_player_grants_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.minecraft_player_grants
    ADD CONSTRAINT minecraft_player_grants_token_hash_key UNIQUE (token_hash);

CREATE INDEX minecraft_player_grants_account_idx ON public.minecraft_player_grants USING btree (account_id, expires_at);

CREATE INDEX minecraft_player_grants_active_installation_idx ON public.minecraft_player_grants USING btree (installation_id, expires_at) WHERE (revoked_at IS NULL);

ALTER TABLE ONLY public.minecraft_player_grants
    ADD CONSTRAINT minecraft_player_grants_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.minecraft_player_grants
    ADD CONSTRAINT minecraft_player_grants_challenge_id_fkey FOREIGN KEY (challenge_id) REFERENCES public.minecraft_player_challenges(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.minecraft_player_grants
    ADD CONSTRAINT minecraft_player_grants_installation_id_fkey FOREIGN KEY (installation_id) REFERENCES public.minecraft_paper_installations(id) ON DELETE CASCADE;

