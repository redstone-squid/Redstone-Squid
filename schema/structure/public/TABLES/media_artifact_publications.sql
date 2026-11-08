CREATE TABLE public.media_artifact_publications (
    object_key text NOT NULL,
    upload_id uuid NOT NULL,
    claim_token uuid NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    renewed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT media_artifact_publications_expiry_after_creation CHECK ((expires_at > created_at))
);

COMMENT ON TABLE public.media_artifact_publications IS 'A crash-recoverable lease protecting one claim''s in-flight object publication.';

ALTER TABLE ONLY public.media_artifact_publications
    ADD CONSTRAINT media_artifact_publications_pkey PRIMARY KEY (object_key, upload_id, claim_token);

CREATE INDEX media_artifact_publications_expiry_idx ON public.media_artifact_publications USING btree (expires_at);

ALTER TABLE ONLY public.media_artifact_publications
    ADD CONSTRAINT media_artifact_publications_object_key_fkey FOREIGN KEY (object_key) REFERENCES public.media_artifact_objects(object_key) ON DELETE CASCADE;

