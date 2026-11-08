CREATE TABLE public.verification_attempts (
    provider text NOT NULL,
    subject text NOT NULL,
    consecutive_failures integer DEFAULT 0 NOT NULL,
    locked_until timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT verification_attempts_failures_non_negative CHECK ((consecutive_failures >= 0)),
    CONSTRAINT verification_attempts_provider_valid CHECK ((provider = ANY (ARRAY['discord'::text, 'java'::text, 'bedrock'::text])))
);

COMMENT ON TABLE public.verification_attempts IS 'Consecutive failed code redemptions for an external identity. A successful redemption resets the count.';

ALTER TABLE ONLY public.verification_attempts
    ADD CONSTRAINT verification_attempts_pkey PRIMARY KEY (provider, subject);

