CREATE TABLE public.record_recompute_queue (
    id bigint NOT NULL,
    scope_key text NOT NULL,
    build_kind text NOT NULL,
    build_id bigint,
    reasons jsonb DEFAULT '[]'::jsonb NOT NULL,
    enqueued_at timestamp with time zone DEFAULT now() NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    claimed_at timestamp with time zone,
    last_error text,
    available_at timestamp with time zone DEFAULT now() NOT NULL,
    claim_token uuid,
    CONSTRAINT record_recompute_queue_attempts_nonnegative CHECK ((attempts >= 0)),
    CONSTRAINT record_recompute_queue_build_kind_check CHECK ((build_kind = ANY (ARRAY['door'::text, 'entrance'::text, 'extender'::text, 'utility'::text]))),
    CONSTRAINT record_recompute_queue_claim_complete CHECK (((claimed_at IS NULL) = (claim_token IS NULL)))
);

COMMENT ON TABLE public.record_recompute_queue IS 'A durable request to recompute an affected record scope.';

COMMENT ON COLUMN public.record_recompute_queue.available_at IS 'Earliest next claim time; retry backoff advances it without changing the enqueue time.';

COMMENT ON COLUMN public.record_recompute_queue.claim_token IS 'Token identifying the current claim, cleared when new work replaces it.

Completion and failure updates match this token to preserve work enqueued
or reclaimed after the worker started.';

ALTER TABLE ONLY public.record_recompute_queue
    ADD CONSTRAINT record_recompute_queue_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.record_recompute_queue
    ADD CONSTRAINT record_recompute_queue_scope_key_key UNIQUE (scope_key);

CREATE INDEX record_recompute_queue_build_idx ON public.record_recompute_queue USING btree (build_id);

CREATE INDEX record_recompute_queue_ready_idx ON public.record_recompute_queue USING btree (available_at) WHERE (claimed_at IS NULL);

ALTER TABLE ONLY public.record_recompute_queue
    ADD CONSTRAINT record_recompute_queue_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON DELETE CASCADE;

