CREATE TABLE public.cli_session_challenges (
    id uuid NOT NULL,
    device_id uuid NOT NULL,
    nonce_hash bytea NOT NULL,
    created_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    consumed_at timestamp with time zone,
    CONSTRAINT cli_session_challenges_consumed_after_creation CHECK (((consumed_at IS NULL) OR (consumed_at >= created_at))),
    CONSTRAINT cli_session_challenges_expiry_after_creation CHECK ((expires_at > created_at)),
    CONSTRAINT cli_session_challenges_nonce_hash_length CHECK ((octet_length(nonce_hash) = 32))
);

COMMENT ON TABLE public.cli_session_challenges IS 'A one-time device proof nonce stored only as a digest.';

ALTER TABLE ONLY public.cli_session_challenges
    ADD CONSTRAINT cli_session_challenges_nonce_hash_key UNIQUE (nonce_hash);

ALTER TABLE ONLY public.cli_session_challenges
    ADD CONSTRAINT cli_session_challenges_pkey PRIMARY KEY (id);

CREATE INDEX cli_session_challenges_active_device_idx ON public.cli_session_challenges USING btree (device_id, expires_at) WHERE (consumed_at IS NULL);

ALTER TABLE ONLY public.cli_session_challenges
    ADD CONSTRAINT cli_session_challenges_device_id_fkey FOREIGN KEY (device_id) REFERENCES public.cli_devices(id) ON DELETE CASCADE;

