CREATE TABLE public.submission_schematic_sources (
    id uuid NOT NULL,
    owner_account_id integer NOT NULL,
    uploaded_by_account_id integer NOT NULL,
    filename text NOT NULL,
    sha256 text,
    byte_size bigint,
    state text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT submission_schematic_sources_sha_check CHECK ((sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT submission_schematic_sources_size_check CHECK (((byte_size IS NULL) OR ((byte_size > 0) AND (byte_size <= 16777216)))),
    CONSTRAINT submission_schematic_sources_state_check CHECK ((state = ANY (ARRAY['uploading'::text, 'waiting_sanitizer'::text, 'failed'::text, 'deleting'::text, 'deleted'::text]))),
    CONSTRAINT submission_schematic_sources_uploaded_check CHECK (((state <> 'waiting_sanitizer'::text) OR ((sha256 IS NOT NULL) AND (byte_size IS NOT NULL))))
);

COMMENT ON TABLE public.submission_schematic_sources IS 'Private source bytes tracked before upload and retained until reference-safe deletion.';

ALTER TABLE ONLY public.submission_schematic_sources
    ADD CONSTRAINT submission_schematic_sources_pkey PRIMARY KEY (id);

CREATE INDEX submission_schematic_sources_actor_idx ON public.submission_schematic_sources USING btree (uploaded_by_account_id);

CREATE INDEX submission_schematic_sources_cleanup_idx ON public.submission_schematic_sources USING btree (updated_at) WHERE (state <> 'deleted'::text);

CREATE INDEX submission_schematic_sources_owner_idx ON public.submission_schematic_sources USING btree (owner_account_id);

ALTER TABLE ONLY public.submission_schematic_sources
    ADD CONSTRAINT submission_schematic_sources_owner_account_id_fkey FOREIGN KEY (owner_account_id) REFERENCES public.accounts(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.submission_schematic_sources
    ADD CONSTRAINT submission_schematic_sources_uploaded_by_account_id_fkey FOREIGN KEY (uploaded_by_account_id) REFERENCES public.accounts(id) ON DELETE RESTRICT;

