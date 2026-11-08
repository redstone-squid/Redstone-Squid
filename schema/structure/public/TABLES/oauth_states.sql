CREATE TABLE public.oauth_states (
    state text NOT NULL,
    code_verifier text NOT NULL,
    redirect_to text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    provider text NOT NULL
);

COMMENT ON TABLE public.oauth_states IS 'One-time OAuth PKCE state shared across API replicas.';

COMMENT ON COLUMN public.oauth_states.provider IS 'The namespace this state was minted for; the callback refuses a mismatch.';

ALTER TABLE ONLY public.oauth_states
    ADD CONSTRAINT oauth_states_pkey PRIMARY KEY (state);

