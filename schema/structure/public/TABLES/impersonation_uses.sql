CREATE TABLE public.impersonation_uses (
    id uuid NOT NULL,
    session_id uuid NOT NULL,
    operation text NOT NULL,
    created_at timestamp with time zone NOT NULL,
    commits integer NOT NULL
);

COMMENT ON TABLE public.impersonation_uses IS 'An authorized impersonated operation tied to its originating session.';

ALTER TABLE ONLY public.impersonation_uses
    ADD CONSTRAINT impersonation_uses_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.impersonation_uses
    ADD CONSTRAINT impersonation_uses_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.impersonation_sessions(id);

