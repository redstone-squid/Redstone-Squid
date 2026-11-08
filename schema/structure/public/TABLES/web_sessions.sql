CREATE TABLE public.web_sessions (
    id uuid NOT NULL,
    token_hash bytea NOT NULL,
    account_id integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    last_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    revoked_at timestamp with time zone,
    user_agent text
);

COMMENT ON TABLE public.web_sessions IS 'A revocable opaque browser session.';

ALTER TABLE ONLY public.web_sessions
    ADD CONSTRAINT web_sessions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.web_sessions
    ADD CONSTRAINT web_sessions_token_hash_key UNIQUE (token_hash);

CREATE INDEX web_sessions_account_idx ON public.web_sessions USING btree (account_id);

CREATE INDEX web_sessions_active_idx ON public.web_sessions USING btree (expires_at) WHERE (revoked_at IS NULL);

ALTER TABLE ONLY public.web_sessions
    ADD CONSTRAINT web_sessions_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;

