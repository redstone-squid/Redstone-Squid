CREATE TABLE public.cli_device_enrollments (
    id uuid NOT NULL,
    device_code_hash bytea NOT NULL,
    user_code_hash bytea NOT NULL,
    public_key bytea NOT NULL,
    client_instance_id uuid NOT NULL,
    label text NOT NULL,
    created_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    approved_by_account_id integer,
    approved_at timestamp with time zone,
    exchanged_at timestamp with time zone,
    revoked_at timestamp with time zone,
    CONSTRAINT cli_device_enrollments_approval_complete CHECK (((approved_by_account_id IS NULL) = (approved_at IS NULL))),
    CONSTRAINT cli_device_enrollments_device_hash_length CHECK ((octet_length(device_code_hash) = 32)),
    CONSTRAINT cli_device_enrollments_exchange_requires_approval CHECK (((exchanged_at IS NULL) OR (approved_at IS NOT NULL))),
    CONSTRAINT cli_device_enrollments_expiry_after_creation CHECK ((expires_at > created_at)),
    CONSTRAINT cli_device_enrollments_label_length CHECK (((char_length(label) >= 1) AND (char_length(label) <= 80))),
    CONSTRAINT cli_device_enrollments_public_key_length CHECK ((octet_length(public_key) = 32)),
    CONSTRAINT cli_device_enrollments_user_hash_length CHECK ((octet_length(user_code_hash) = 32))
);

COMMENT ON TABLE public.cli_device_enrollments IS 'A browser-approved enrollment storing only digests of bearer codes.';

ALTER TABLE ONLY public.cli_device_enrollments
    ADD CONSTRAINT cli_device_enrollments_device_code_hash_key UNIQUE (device_code_hash);

ALTER TABLE ONLY public.cli_device_enrollments
    ADD CONSTRAINT cli_device_enrollments_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.cli_device_enrollments
    ADD CONSTRAINT cli_device_enrollments_user_code_hash_key UNIQUE (user_code_hash);

CREATE INDEX cli_device_enrollments_active_client_idx ON public.cli_device_enrollments USING btree (client_instance_id, expires_at) WHERE ((exchanged_at IS NULL) AND (revoked_at IS NULL));

CREATE INDEX cli_device_enrollments_approved_by_idx ON public.cli_device_enrollments USING btree (approved_by_account_id);

ALTER TABLE ONLY public.cli_device_enrollments
    ADD CONSTRAINT cli_device_enrollments_approved_account_id_fkey FOREIGN KEY (approved_by_account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;

