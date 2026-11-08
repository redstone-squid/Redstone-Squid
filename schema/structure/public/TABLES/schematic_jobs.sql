CREATE TABLE public.schematic_jobs (
    id bigint NOT NULL,
    operation text NOT NULL,
    params jsonb NOT NULL,
    input_keys text[] NOT NULL,
    result jsonb,
    result_object_key text,
    attempts integer DEFAULT 0 NOT NULL,
    available_at timestamp with time zone DEFAULT now() NOT NULL,
    claimed_at timestamp with time zone,
    completed_at timestamp with time zone,
    dead_at timestamp with time zone,
    expires_at timestamp with time zone,
    last_error text,
    error_kind text,
    error_context jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    claim_token uuid,
    result_object_keys text[] DEFAULT '{}'::text[] NOT NULL,
    CONSTRAINT schematic_jobs_attempts_nonnegative CHECK ((attempts >= 0)),
    CONSTRAINT schematic_jobs_claim_complete CHECK (((claimed_at IS NULL) = (claim_token IS NULL))),
    CONSTRAINT schematic_jobs_operation_check CHECK ((operation = ANY (ARRAY['capabilities'::text, 'analyze'::text, 'convert'::text, 'compare'::text, 'render'::text, 'simulate'::text, 'autostack'::text]))),
    CONSTRAINT schematic_jobs_single_terminal_state CHECK (((completed_at IS NULL) OR (dead_at IS NULL)))
);

COMMENT ON TABLE public.schematic_jobs IS 'A durable native-engine request whose result remains available until retention expires.

Claim tokens reject stale worker updates. Binary output objects are retained alongside the row
for cleanup; input objects can be shared by multiple jobs.';

COMMENT ON COLUMN public.schematic_jobs.claim_token IS 'Token that must still match when the claiming worker records its outcome.';

COMMENT ON COLUMN public.schematic_jobs.result_object_keys IS 'Result objects registered by worker claims, retained until job expiry.';

ALTER TABLE ONLY public.schematic_jobs ALTER COLUMN id SET DEFAULT nextval('public.schematic_jobs_id_seq'::regclass);

ALTER TABLE ONLY public.schematic_jobs
    ADD CONSTRAINT schematic_jobs_pkey PRIMARY KEY (id);

CREATE INDEX schematic_jobs_expiry_idx ON public.schematic_jobs USING btree (expires_at) WHERE (expires_at IS NOT NULL);

CREATE INDEX schematic_jobs_ready_idx ON public.schematic_jobs USING btree (available_at) WHERE ((completed_at IS NULL) AND (dead_at IS NULL));

