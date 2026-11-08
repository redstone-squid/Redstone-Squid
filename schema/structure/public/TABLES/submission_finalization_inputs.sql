CREATE TABLE public.submission_finalization_inputs (
    job_id uuid NOT NULL,
    payload jsonb NOT NULL,
    payload_sha256 text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT submission_finalization_inputs_payload_object_check CHECK ((jsonb_typeof(payload) = 'object'::text)),
    CONSTRAINT submission_finalization_inputs_payload_sha256_check CHECK ((payload_sha256 ~ '^[0-9a-f]{64}$'::text))
);

COMMENT ON TABLE public.submission_finalization_inputs IS 'Original normalized input retained independently of mutable execution state.';

ALTER TABLE ONLY public.submission_finalization_inputs
    ADD CONSTRAINT submission_finalization_inputs_pkey PRIMARY KEY (job_id);

CREATE TRIGGER submission_finalization_inputs_immutable BEFORE UPDATE ON public.submission_finalization_inputs FOR EACH ROW EXECUTE FUNCTION public.reject_submission_finalization_input_update();

ALTER TABLE ONLY public.submission_finalization_inputs
    ADD CONSTRAINT submission_finalization_inputs_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.submission_finalization_jobs(id) ON DELETE CASCADE;

