CREATE TABLE public.schematic_render_queue (
    build_id bigint NOT NULL,
    enqueued_at timestamp with time zone DEFAULT now() NOT NULL,
    claimed_at timestamp with time zone,
    dead_at timestamp with time zone,
    attempts integer DEFAULT 0 NOT NULL,
    last_error text,
    available_at timestamp with time zone DEFAULT now() NOT NULL,
    claim_token uuid,
    CONSTRAINT schematic_render_queue_attempts_nonnegative CHECK ((attempts >= 0)),
    CONSTRAINT schematic_render_queue_claim_complete CHECK (((claimed_at IS NULL) = (claim_token IS NULL)))
);

COMMENT ON TABLE public.schematic_render_queue IS 'A durable request to render and publish one build''s primary schematic.';

COMMENT ON COLUMN public.schematic_render_queue.available_at IS 'Earliest time a worker may claim this job, including retry backoff.';

COMMENT ON COLUMN public.schematic_render_queue.claim_token IS 'Token that must still match when the claiming worker records its outcome.';

ALTER TABLE ONLY public.schematic_render_queue
    ADD CONSTRAINT schematic_render_queue_pkey PRIMARY KEY (build_id);

CREATE INDEX schematic_render_queue_ready_idx ON public.schematic_render_queue USING btree (available_at) WHERE ((claimed_at IS NULL) AND (dead_at IS NULL));

ALTER TABLE ONLY public.schematic_render_queue
    ADD CONSTRAINT schematic_render_queue_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON DELETE CASCADE;

