CREATE TABLE public.idempotency_requests (
    id uuid NOT NULL,
    principal text NOT NULL,
    idempotency_key text NOT NULL,
    request_fingerprint bytea NOT NULL,
    method text NOT NULL,
    route text NOT NULL,
    state text DEFAULT 'in_progress'::text NOT NULL,
    response_status smallint,
    response_headers jsonb,
    response_body_ciphertext bytea,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    expires_at timestamp with time zone NOT NULL,
    response_body_key_id text,
    response_body_nonce bytea,
    caller text NOT NULL,
    CONSTRAINT idempotency_requests_method_check CHECK ((method = ANY (ARRAY['POST'::text, 'PUT'::text, 'PATCH'::text, 'DELETE'::text]))),
    CONSTRAINT idempotency_requests_response_state_check CHECK ((((state = 'in_progress'::text) AND (response_status IS NULL) AND (response_headers IS NULL) AND (response_body_ciphertext IS NULL) AND (response_body_key_id IS NULL) AND (response_body_nonce IS NULL) AND (completed_at IS NULL)) OR ((state = 'completed'::text) AND (response_status IS NOT NULL) AND (response_headers IS NOT NULL) AND (response_body_ciphertext IS NOT NULL) AND (response_body_key_id IS NOT NULL) AND (response_body_nonce IS NOT NULL) AND (completed_at IS NOT NULL)))),
    CONSTRAINT idempotency_requests_state_check CHECK ((state = ANY (ARRAY['in_progress'::text, 'completed'::text])))
);

COMMENT ON TABLE public.idempotency_requests IS 'One caller-scoped mutation reservation and its completed HTTP response.';

COMMENT ON COLUMN public.idempotency_requests.principal IS 'Legacy copy of caller, still written by the repository for the retained database column.';

COMMENT ON COLUMN public.idempotency_requests.caller IS 'The server-derived caller namespace a key is reserved in.';

ALTER TABLE ONLY public.idempotency_requests
    ADD CONSTRAINT idempotency_requests_caller_key UNIQUE (caller, idempotency_key);

ALTER TABLE ONLY public.idempotency_requests
    ADD CONSTRAINT idempotency_requests_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.idempotency_requests
    ADD CONSTRAINT idempotency_requests_principal_key UNIQUE (principal, idempotency_key);

CREATE INDEX idempotency_requests_expires_at_idx ON public.idempotency_requests USING btree (expires_at);

