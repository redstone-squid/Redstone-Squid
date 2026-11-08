CREATE TABLE public.schematic_preview_objects (
    object_key text NOT NULL,
    byte_size bigint NOT NULL,
    sha256 text,
    ready_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    last_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    cleanup_claimed_at timestamp with time zone,
    cleanup_claim_token uuid,
    CONSTRAINT schematic_preview_objects_cleanup_claim_pair CHECK (((cleanup_claimed_at IS NULL) = (cleanup_claim_token IS NULL))),
    CONSTRAINT schematic_preview_objects_sha256_check CHECK (((sha256 IS NULL) OR (sha256 ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT schematic_preview_objects_size_positive CHECK ((byte_size > 0))
);

COMMENT ON TABLE public.schematic_preview_objects IS 'Durable upload and cleanup state for one generated preview object.';

COMMENT ON COLUMN public.schematic_preview_objects.cleanup_claimed_at IS 'When cleanup reserved this unreferenced object for deletion.';

COMMENT ON COLUMN public.schematic_preview_objects.cleanup_claim_token IS 'Token required to finish cleanup; its presence prevents reuse or publication.';

ALTER TABLE ONLY public.schematic_preview_objects
    ADD CONSTRAINT schematic_preview_objects_pkey PRIMARY KEY (object_key);

CREATE INDEX schematic_preview_objects_cleanup_idx ON public.schematic_preview_objects USING btree (last_seen_at);

