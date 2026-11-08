CREATE TABLE public.cli_devices (
    id uuid NOT NULL,
    account_id integer NOT NULL,
    public_key bytea NOT NULL,
    client_instance_id uuid NOT NULL,
    label text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    last_used_at timestamp with time zone DEFAULT now() NOT NULL,
    revoked_at timestamp with time zone,
    CONSTRAINT cli_devices_label_length CHECK (((char_length(label) >= 1) AND (char_length(label) <= 80))),
    CONSTRAINT cli_devices_last_used_after_creation CHECK ((last_used_at >= created_at)),
    CONSTRAINT cli_devices_public_key_length CHECK ((octet_length(public_key) = 32))
);

COMMENT ON TABLE public.cli_devices IS 'An account-owned Ed25519 CLI device.';

ALTER TABLE ONLY public.cli_devices
    ADD CONSTRAINT cli_devices_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.cli_devices
    ADD CONSTRAINT cli_devices_public_key_key UNIQUE (public_key);

CREATE INDEX cli_devices_account_idx ON public.cli_devices USING btree (account_id, created_at);

CREATE INDEX cli_devices_active_account_idx ON public.cli_devices USING btree (account_id, last_used_at) WHERE (revoked_at IS NULL);

ALTER TABLE ONLY public.cli_devices
    ADD CONSTRAINT cli_devices_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;

