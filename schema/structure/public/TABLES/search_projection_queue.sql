CREATE TABLE public.search_projection_queue (
    id bigint NOT NULL,
    resource_kind text NOT NULL,
    source_key text NOT NULL,
    action text DEFAULT 'upsert'::text NOT NULL,
    enqueued_at timestamp with time zone DEFAULT now() NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    claimed_at timestamp with time zone,
    last_error text,
    dead_at timestamp with time zone,
    available_at timestamp with time zone DEFAULT now() NOT NULL,
    claim_token uuid,
    CONSTRAINT search_projection_queue_action_check CHECK ((action = ANY (ARRAY['upsert'::text, 'delete'::text]))),
    CONSTRAINT search_projection_queue_attempts_nonnegative CHECK ((attempts >= 0)),
    CONSTRAINT search_projection_queue_claim_complete CHECK (((claimed_at IS NULL) = (claim_token IS NULL)))
);

COMMENT ON TABLE public.search_projection_queue IS 'A durable request to refresh or delete a projected search resource.';

COMMENT ON COLUMN public.search_projection_queue.available_at IS 'Earliest time a worker may claim this item; retries move it into the future.';

COMMENT ON COLUMN public.search_projection_queue.claim_token IS 'Token generated for the current claim; stale workers cannot acknowledge a newer claim.';

ALTER TABLE ONLY public.search_projection_queue
    ADD CONSTRAINT search_projection_queue_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.search_projection_queue
    ADD CONSTRAINT search_projection_queue_resource_key UNIQUE (resource_kind, source_key);

CREATE INDEX search_projection_queue_ready_idx ON public.search_projection_queue USING btree (available_at) WHERE ((claimed_at IS NULL) AND (dead_at IS NULL));

