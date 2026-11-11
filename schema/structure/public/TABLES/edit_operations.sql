CREATE TABLE public.edit_operations (
    id uuid NOT NULL,
    actor text NOT NULL,
    fingerprint bytea NOT NULL,
    revisions jsonb NOT NULL,
    changed boolean NOT NULL,
    final boolean NOT NULL,
    created_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    CONSTRAINT edit_operations_fingerprint_length CHECK ((octet_length(fingerprint) = 32))
);

ALTER TABLE ONLY public.edit_operations
    ADD CONSTRAINT edit_operations_pkey PRIMARY KEY (id);

CREATE INDEX edit_operations_expiry_idx ON public.edit_operations USING btree (expires_at);

