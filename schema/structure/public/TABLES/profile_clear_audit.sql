CREATE TABLE public.profile_clear_audit (
    operation_id uuid NOT NULL,
    account_id integer NOT NULL,
    actor_account_id integer NOT NULL,
    revision bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

COMMENT ON TABLE public.profile_clear_audit IS 'Retain staff attribution without retaining the removed profile content.';

ALTER TABLE ONLY public.profile_clear_audit
    ADD CONSTRAINT profile_clear_audit_pkey PRIMARY KEY (operation_id);

