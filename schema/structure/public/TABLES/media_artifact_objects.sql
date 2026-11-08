CREATE TABLE public.media_artifact_objects (
    object_key text NOT NULL,
    sha256 text NOT NULL,
    byte_size bigint NOT NULL,
    first_upload_id uuid NOT NULL,
    last_upload_id uuid NOT NULL,
    available_at timestamp with time zone DEFAULT now() NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    last_error text,
    deleted_at timestamp with time zone,
    cleanup_claimed_at timestamp with time zone,
    cleanup_claim_token uuid,
    first_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    last_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT media_artifact_objects_attempts_nonnegative CHECK ((attempts >= 0)),
    CONSTRAINT media_artifact_objects_cleanup_claim_shape CHECK (((cleanup_claimed_at IS NULL) = (cleanup_claim_token IS NULL))),
    CONSTRAINT media_artifact_objects_sha256_check CHECK ((sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT media_artifact_objects_size_positive CHECK ((byte_size > 0))
);

COMMENT ON TABLE public.media_artifact_objects IS 'Durable lifecycle and cleanup audit state for one content-addressed object.';

ALTER TABLE ONLY public.media_artifact_objects
    ADD CONSTRAINT media_artifact_objects_pkey PRIMARY KEY (object_key);

CREATE INDEX media_artifact_objects_cleanup_idx ON public.media_artifact_objects USING btree (available_at) WHERE (deleted_at IS NULL);

