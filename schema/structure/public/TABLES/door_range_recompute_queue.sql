CREATE TABLE public.door_range_recompute_queue (
    query_id bigint NOT NULL,
    enqueued_at timestamp with time zone DEFAULT now() NOT NULL,
    available_at timestamp with time zone DEFAULT now() NOT NULL,
    claimed_at timestamp with time zone,
    claim_token uuid,
    attempts integer DEFAULT 0 NOT NULL,
    last_error text,
    CONSTRAINT door_range_recompute_queue_attempts_nonnegative CHECK ((attempts >= 0)),
    CONSTRAINT door_range_recompute_queue_claim_complete CHECK (((claimed_at IS NULL) = (claim_token IS NULL)))
);

COMMENT ON TABLE public.door_range_recompute_queue IS 'One independently retried refresh request per registered door range.';

ALTER TABLE ONLY public.door_range_recompute_queue
    ADD CONSTRAINT door_range_recompute_queue_pkey PRIMARY KEY (query_id);

ALTER TABLE ONLY public.door_range_recompute_queue
    ADD CONSTRAINT door_range_recompute_queue_query_id_fkey FOREIGN KEY (query_id) REFERENCES public.door_range_registrations(id) ON DELETE CASCADE;

