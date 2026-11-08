CREATE TABLE public.minecraft_player_challenges (
    id uuid NOT NULL,
    device_code_hash bytea NOT NULL,
    user_code_hash bytea NOT NULL,
    origin text NOT NULL,
    java_uuid uuid NOT NULL,
    installation_id uuid,
    installation_credential_version integer,
    pkce_s256_challenge text,
    created_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    approved_by_account_id integer,
    approved_at timestamp with time zone,
    exchanged_at timestamp with time zone,
    revoked_at timestamp with time zone,
    CONSTRAINT minecraft_player_challenges_approval_complete CHECK (((approved_by_account_id IS NULL) = (approved_at IS NULL))),
    CONSTRAINT minecraft_player_challenges_device_hash_length CHECK ((octet_length(device_code_hash) = 32)),
    CONSTRAINT minecraft_player_challenges_exchange_requires_approval CHECK (((exchanged_at IS NULL) OR (approved_at IS NOT NULL))),
    CONSTRAINT minecraft_player_challenges_expiry_after_creation CHECK ((expires_at > created_at)),
    CONSTRAINT minecraft_player_challenges_origin_binding CHECK ((((origin = 'paper'::text) AND (installation_id IS NOT NULL) AND (installation_credential_version IS NOT NULL) AND (pkce_s256_challenge IS NULL)) OR ((origin = 'fabric'::text) AND (installation_id IS NULL) AND (installation_credential_version IS NULL) AND (pkce_s256_challenge IS NOT NULL)))),
    CONSTRAINT minecraft_player_challenges_origin_check CHECK ((origin = ANY (ARRAY['paper'::text, 'fabric'::text]))),
    CONSTRAINT minecraft_player_challenges_pkce_format CHECK (((pkce_s256_challenge IS NULL) OR (pkce_s256_challenge ~ '^[A-Za-z0-9_-]{43}$'::text))),
    CONSTRAINT minecraft_player_challenges_user_hash_length CHECK ((octet_length(user_code_hash) = 32))
);

COMMENT ON TABLE public.minecraft_player_challenges IS 'A short-lived device flow storing only hashes of both bearer codes.';

ALTER TABLE ONLY public.minecraft_player_challenges
    ADD CONSTRAINT minecraft_player_challenges_device_code_hash_key UNIQUE (device_code_hash);

ALTER TABLE ONLY public.minecraft_player_challenges
    ADD CONSTRAINT minecraft_player_challenges_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.minecraft_player_challenges
    ADD CONSTRAINT minecraft_player_challenges_user_code_hash_key UNIQUE (user_code_hash);

CREATE INDEX minecraft_player_challenges_active_lookup_idx ON public.minecraft_player_challenges USING btree (origin, java_uuid, installation_id, expires_at) WHERE ((exchanged_at IS NULL) AND (revoked_at IS NULL));

CREATE INDEX minecraft_player_challenges_approved_by_idx ON public.minecraft_player_challenges USING btree (approved_by_account_id);

ALTER TABLE ONLY public.minecraft_player_challenges
    ADD CONSTRAINT minecraft_player_challenges_approved_account_id_fkey FOREIGN KEY (approved_by_account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.minecraft_player_challenges
    ADD CONSTRAINT minecraft_player_challenges_installation_id_fkey FOREIGN KEY (installation_id) REFERENCES public.minecraft_paper_installations(id) ON DELETE CASCADE;

