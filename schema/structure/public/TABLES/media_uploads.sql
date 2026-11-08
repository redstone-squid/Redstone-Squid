CREATE TABLE public.media_uploads (
    id uuid NOT NULL,
    draft_id uuid NOT NULL,
    kind text NOT NULL,
    source_content_type text NOT NULL,
    source_byte_size bigint NOT NULL,
    source_sha256 text NOT NULL,
    source_object_key text NOT NULL,
    strip_audio boolean DEFAULT false NOT NULL,
    raw_deleted_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT media_uploads_audio_only_for_video CHECK (((kind = 'video'::text) OR (strip_audio = false))),
    CONSTRAINT media_uploads_content_type_length CHECK (((char_length(source_content_type) >= 1) AND (char_length(source_content_type) <= 255))),
    CONSTRAINT media_uploads_kind_check CHECK ((kind = ANY (ARRAY['image'::text, 'video'::text]))),
    CONSTRAINT media_uploads_source_sha256_check CHECK ((source_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT media_uploads_source_size_positive CHECK ((source_byte_size > 0))
);

COMMENT ON TABLE public.media_uploads IS 'Immutable raw-upload metadata used to verify worker input.';

ALTER TABLE ONLY public.media_uploads
    ADD CONSTRAINT media_uploads_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.media_uploads
    ADD CONSTRAINT media_uploads_source_object_key_key UNIQUE (source_object_key);

CREATE INDEX media_uploads_draft_idx ON public.media_uploads USING btree (draft_id, created_at);

CREATE INDEX media_uploads_raw_cleanup_idx ON public.media_uploads USING btree (created_at) WHERE (raw_deleted_at IS NULL);

