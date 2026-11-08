CREATE TABLE public.submission_inference_runs (
    id uuid NOT NULL,
    owner_account_id integer NOT NULL,
    inputs jsonb NOT NULL,
    state text NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    claim_token uuid,
    claim_expires_at timestamp with time zone,
    candidates jsonb NOT NULL,
    CONSTRAINT submission_inference_runs_state_check CHECK ((state = ANY (ARRAY['processing'::text, 'failed'::text, 'completed'::text])))
);

COMMENT ON TABLE public.submission_inference_runs IS 'Private inference inputs, candidate results, and the current expiring worker claim.';

ALTER TABLE ONLY public.submission_inference_runs
    ADD CONSTRAINT submission_inference_runs_pkey PRIMARY KEY (id);

CREATE INDEX ix_submission_inference_runs_expires_at ON public.submission_inference_runs USING btree (expires_at);

CREATE INDEX ix_submission_inference_runs_owner_account_id ON public.submission_inference_runs USING btree (owner_account_id);

CREATE TRIGGER submission_runs_enqueue_status AFTER INSERT OR DELETE OR UPDATE ON public.submission_inference_runs FOR EACH ROW EXECUTE FUNCTION public.enqueue_submission_status();

ALTER TABLE ONLY public.submission_inference_runs
    ADD CONSTRAINT submission_inference_runs_owner_account_id_fkey FOREIGN KEY (owner_account_id) REFERENCES public.accounts(id) ON DELETE RESTRICT;

