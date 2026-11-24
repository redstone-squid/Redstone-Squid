CREATE TABLE public.impersonation_sessions (
    id uuid NOT NULL,
    operator_id integer NOT NULL,
    target_id integer NOT NULL,
    target_discord_id bigint,
    binding text NOT NULL,
    reason text NOT NULL,
    created_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone
);

COMMENT ON TABLE public.impersonation_sessions IS 'A debugging session bound to its operator''s authenticated entry point.';

ALTER TABLE ONLY public.impersonation_sessions
    ADD CONSTRAINT impersonation_sessions_pkey PRIMARY KEY (id);

