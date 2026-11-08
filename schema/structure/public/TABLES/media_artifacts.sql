CREATE TABLE public.media_artifacts (
    id bigint NOT NULL,
    upload_id uuid NOT NULL,
    role text NOT NULL,
    object_key text NOT NULL,
    content_type text NOT NULL,
    byte_size bigint NOT NULL,
    sha256 text NOT NULL,
    width integer,
    height integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT media_artifacts_dimensions_by_role CHECK ((((role = 'report'::text) AND (width IS NULL) AND (height IS NULL)) OR ((role <> 'report'::text) AND (width > 0) AND (height > 0)))),
    CONSTRAINT media_artifacts_role_check CHECK ((role = ANY (ARRAY['output'::text, 'poster'::text, 'video_thumbnail'::text, 'report'::text]))),
    CONSTRAINT media_artifacts_sha256_check CHECK ((sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT media_artifacts_size_positive CHECK ((byte_size > 0))
);

COMMENT ON TABLE public.media_artifacts IS 'Content-addressed normalized output, video thumbnail, or report metadata.';

ALTER TABLE ONLY public.media_artifacts
    ADD CONSTRAINT media_artifacts_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.media_artifacts
    ADD CONSTRAINT media_artifacts_upload_role_key UNIQUE (upload_id, role);

CREATE INDEX media_artifacts_object_key_idx ON public.media_artifacts USING btree (object_key);

CREATE INDEX media_artifacts_sha256_idx ON public.media_artifacts USING btree (sha256);

ALTER TABLE ONLY public.media_artifacts
    ADD CONSTRAINT media_artifacts_object_key_fkey FOREIGN KEY (object_key) REFERENCES public.media_artifact_objects(object_key);

ALTER TABLE ONLY public.media_artifacts
    ADD CONSTRAINT media_artifacts_upload_id_fkey FOREIGN KEY (upload_id) REFERENCES public.media_uploads(id) ON DELETE CASCADE;

