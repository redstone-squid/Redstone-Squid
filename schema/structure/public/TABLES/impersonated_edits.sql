CREATE TABLE public.impersonated_edits (
    operation_id uuid NOT NULL,
    use_id uuid NOT NULL,
    revisions jsonb NOT NULL
);

COMMENT ON TABLE public.impersonated_edits IS 'Committed edit and resource revisions retained with the operator audit.';

ALTER TABLE ONLY public.impersonated_edits
    ADD CONSTRAINT impersonated_edits_pkey PRIMARY KEY (operation_id);

ALTER TABLE ONLY public.impersonated_edits
    ADD CONSTRAINT impersonated_edits_use_id_fkey FOREIGN KEY (use_id) REFERENCES public.impersonation_uses(id);

