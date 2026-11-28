CREATE TABLE public.build_submissions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    build_id bigint NOT NULL,
    submitter_account_id integer NOT NULL,
    submitted_at timestamp with time zone DEFAULT now() NOT NULL,
    finalization_job_id uuid,
    edit_vote_id bigint,
    base_revision bigint,
    content jsonb NOT NULL,
    decision text DEFAULT 'pending'::text NOT NULL,
    decided_revision bigint,
    feedback text,
    CONSTRAINT build_submissions_origin_check CHECK ((((edit_vote_id IS NULL) AND (base_revision IS NULL)) OR ((finalization_job_id IS NULL) AND (edit_vote_id IS NOT NULL) AND (base_revision IS NOT NULL) AND (base_revision > 0))))
);

COMMENT ON TABLE public.build_submissions IS 'A submitted version, retained independently of its draft and voting session.';

ALTER TABLE ONLY public.build_submissions
    ADD CONSTRAINT build_submissions_edit_vote_id_key UNIQUE (edit_vote_id);

ALTER TABLE ONLY public.build_submissions
    ADD CONSTRAINT build_submissions_finalization_job_id_key UNIQUE (finalization_job_id);

ALTER TABLE ONLY public.build_submissions
    ADD CONSTRAINT build_submissions_pkey PRIMARY KEY (id);

CREATE INDEX build_submissions_build_idx ON public.build_submissions USING btree (build_id);

CREATE UNIQUE INDEX build_submissions_new_build_idx ON public.build_submissions USING btree (build_id) WHERE (edit_vote_id IS NULL);

CREATE INDEX build_submissions_submitter_idx ON public.build_submissions USING btree (submitter_account_id, submitted_at);

ALTER TABLE ONLY public.build_submissions
    ADD CONSTRAINT build_submissions_submitter_account_id_fkey FOREIGN KEY (submitter_account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;

