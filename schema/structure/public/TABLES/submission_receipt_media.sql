CREATE TABLE public.submission_receipt_media (
    job_id uuid NOT NULL,
    upload_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

COMMENT ON TABLE public.submission_receipt_media IS 'Normalized uploads retained by a committed submission receipt.';

ALTER TABLE ONLY public.submission_receipt_media
    ADD CONSTRAINT submission_receipt_media_pkey PRIMARY KEY (job_id, upload_id);

CREATE INDEX submission_receipt_media_upload_idx ON public.submission_receipt_media USING btree (upload_id);

ALTER TABLE ONLY public.submission_receipt_media
    ADD CONSTRAINT submission_receipt_media_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.submission_finalization_results(job_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.submission_receipt_media
    ADD CONSTRAINT submission_receipt_media_upload_id_fkey FOREIGN KEY (upload_id) REFERENCES public.media_uploads(id) ON DELETE RESTRICT;

