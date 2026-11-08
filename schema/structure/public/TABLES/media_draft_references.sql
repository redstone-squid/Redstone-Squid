CREATE TABLE public.media_draft_references (
    draft_id uuid NOT NULL,
    upload_id uuid NOT NULL,
    discarded boolean DEFAULT false NOT NULL
);

COMMENT ON TABLE public.media_draft_references IS 'One independently discardable draft reference to a shared normalization job.';

ALTER TABLE ONLY public.media_draft_references
    ADD CONSTRAINT media_draft_references_pkey PRIMARY KEY (draft_id, upload_id);

CREATE INDEX media_draft_references_upload_idx ON public.media_draft_references USING btree (upload_id);

ALTER TABLE ONLY public.media_draft_references
    ADD CONSTRAINT media_draft_references_draft_id_fkey FOREIGN KEY (draft_id) REFERENCES public.submission_drafts(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.media_draft_references
    ADD CONSTRAINT media_draft_references_upload_id_fkey FOREIGN KEY (upload_id) REFERENCES public.media_uploads(id) ON DELETE RESTRICT;

