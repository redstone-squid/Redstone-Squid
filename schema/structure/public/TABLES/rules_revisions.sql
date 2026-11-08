CREATE TABLE public.rules_revisions (
    id bigint NOT NULL,
    document_hash text NOT NULL,
    calculator_version text NOT NULL,
    formatter_version text NOT NULL,
    activated_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

COMMENT ON TABLE public.rules_revisions IS 'Calculator and formatter versions with a mutable activation timestamp.';

ALTER TABLE ONLY public.rules_revisions
    ADD CONSTRAINT rules_revisions_content_key UNIQUE (document_hash, calculator_version, formatter_version);

ALTER TABLE ONLY public.rules_revisions
    ADD CONSTRAINT rules_revisions_pkey PRIMARY KEY (id);

