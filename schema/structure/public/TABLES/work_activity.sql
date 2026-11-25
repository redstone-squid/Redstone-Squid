CREATE TABLE public.work_activity (
    id bigint NOT NULL,
    occurred_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    relation text NOT NULL,
    item jsonb NOT NULL,
    transition text NOT NULL,
    acquired_claim text,
    parent_claim text,
    invocation_id uuid,
    operator text,
    effective text,
    credential_id text,
    impersonation_session_id uuid,
    impersonation_use_id uuid
);

COMMENT ON TABLE public.work_activity IS 'Committed queue transitions and event publications with their executing invocation.';

ALTER TABLE ONLY public.work_activity
    ADD CONSTRAINT work_activity_pkey PRIMARY KEY (id);

CREATE INDEX work_activity_claim_idx ON public.work_activity USING btree (acquired_claim) WHERE (acquired_claim IS NOT NULL);

CREATE INDEX work_activity_invocation_idx ON public.work_activity USING btree (invocation_id);

CREATE INDEX work_activity_item_idx ON public.work_activity USING btree (relation, item, id);

