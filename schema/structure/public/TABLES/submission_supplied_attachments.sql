CREATE TABLE public.submission_supplied_attachments (
    draft_id uuid NOT NULL,
    source_id uuid NOT NULL,
    filename text NOT NULL,
    kind text NOT NULL,
    status text NOT NULL,
    discarded boolean DEFAULT false NOT NULL,
    CONSTRAINT submission_supplied_attachments_kind_check CHECK ((kind = ANY (ARRAY['schematic'::text, 'image'::text, 'video'::text, 'unknown'::text]))),
    CONSTRAINT submission_supplied_attachments_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'ready'::text, 'failed'::text, 'discarded'::text])))
);

COMMENT ON TABLE public.submission_supplied_attachments IS 'A supplied file retained before download and resolved only by registration or explicit discard.';

ALTER TABLE ONLY public.submission_supplied_attachments
    ADD CONSTRAINT submission_supplied_attachments_pkey PRIMARY KEY (draft_id, source_id);

CREATE TRIGGER submission_intake_enqueue_status AFTER INSERT OR DELETE OR UPDATE ON public.submission_supplied_attachments FOR EACH ROW EXECUTE FUNCTION public.enqueue_submission_status();

ALTER TABLE ONLY public.submission_supplied_attachments
    ADD CONSTRAINT submission_supplied_attachments_draft_id_fkey FOREIGN KEY (draft_id) REFERENCES public.submission_drafts(id) ON DELETE CASCADE;

