CREATE TABLE public.edit_resource_leases (
    operation_id uuid NOT NULL,
    resource text NOT NULL,
    expires_at timestamp with time zone NOT NULL
);

ALTER TABLE ONLY public.edit_resource_leases
    ADD CONSTRAINT edit_resource_leases_pkey PRIMARY KEY (operation_id, resource);

CREATE INDEX edit_resource_leases_resource_expiry_idx ON public.edit_resource_leases USING btree (resource, expires_at);

