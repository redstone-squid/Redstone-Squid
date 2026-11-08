CREATE TABLE public.cli_sessions (
    id uuid NOT NULL,
    device_id uuid NOT NULL,
    token_hash bytea NOT NULL,
    issued_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    last_seen_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone,
    CONSTRAINT cli_sessions_expiry_after_issue CHECK ((expires_at > issued_at)),
    CONSTRAINT cli_sessions_last_seen_after_issue CHECK ((last_seen_at >= issued_at)),
    CONSTRAINT cli_sessions_token_hash_length CHECK ((octet_length(token_hash) = 32))
);

COMMENT ON TABLE public.cli_sessions IS 'A short-lived CLI bearer session storing only its token digest.';

ALTER TABLE ONLY public.cli_sessions
    ADD CONSTRAINT cli_sessions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.cli_sessions
    ADD CONSTRAINT cli_sessions_token_hash_key UNIQUE (token_hash);

CREATE INDEX cli_sessions_active_device_idx ON public.cli_sessions USING btree (device_id, expires_at) WHERE (revoked_at IS NULL);

CREATE INDEX cli_sessions_device_idx ON public.cli_sessions USING btree (device_id, expires_at);

ALTER TABLE ONLY public.cli_sessions
    ADD CONSTRAINT cli_sessions_device_id_fkey FOREIGN KEY (device_id) REFERENCES public.cli_devices(id) ON DELETE CASCADE;

