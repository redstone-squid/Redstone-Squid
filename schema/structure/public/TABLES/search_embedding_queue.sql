CREATE TABLE public.search_embedding_queue (
    document_id bigint NOT NULL,
    source_hash text NOT NULL,
    enqueued_at timestamp with time zone DEFAULT now() NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    claimed_at timestamp with time zone,
    last_error text,
    dead_at timestamp with time zone,
    available_at timestamp with time zone DEFAULT now() NOT NULL,
    claim_token uuid,
    CONSTRAINT search_embedding_queue_attempts_nonnegative CHECK ((attempts >= 0)),
    CONSTRAINT search_embedding_queue_claim_complete CHECK (((claimed_at IS NULL) = (claim_token IS NULL)))
);

COMMENT ON TABLE public.search_embedding_queue IS 'A durable request to embed a search document whose source hash changed.';

COMMENT ON COLUMN public.search_embedding_queue.available_at IS 'Earliest time a worker may claim this item; retries move it into the future.';

COMMENT ON COLUMN public.search_embedding_queue.claim_token IS 'Token generated for the current claim; stale workers cannot acknowledge a newer claim.';

ALTER TABLE ONLY public.search_embedding_queue
    ADD CONSTRAINT search_embedding_queue_pkey PRIMARY KEY (document_id);

CREATE INDEX search_embedding_queue_ready_idx ON public.search_embedding_queue USING btree (available_at) WHERE ((claimed_at IS NULL) AND (dead_at IS NULL));

ALTER TABLE ONLY public.search_embedding_queue
    ADD CONSTRAINT search_embedding_queue_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.search_documents(id) ON DELETE CASCADE;

