CREATE TABLE public.discord_sync_queue (
    id bigint NOT NULL,
    resource_kind text NOT NULL,
    source_key text NOT NULL,
    action text DEFAULT 'refresh'::text NOT NULL,
    enqueued_at timestamp with time zone DEFAULT now() NOT NULL,
    claimed_at timestamp with time zone,
    attempts integer DEFAULT 0 NOT NULL,
    last_error text,
    dead_at timestamp with time zone,
    generation bigint DEFAULT nextval('public.discord_sync_generation_seq'::regclass) NOT NULL,
    available_at timestamp with time zone DEFAULT now() NOT NULL,
    claim_token uuid,
    CONSTRAINT discord_sync_queue_action_check CHECK ((action = ANY (ARRAY['refresh'::text, 'delete'::text]))),
    CONSTRAINT discord_sync_queue_attempts_nonnegative CHECK ((attempts >= 0)),
    CONSTRAINT discord_sync_queue_claim_complete CHECK (((claimed_at IS NULL) = (claim_token IS NULL))),
    CONSTRAINT discord_sync_queue_resource_kind_check CHECK ((resource_kind = ANY (ARRAY['build'::text, 'vote_session'::text, 'starboard_entry'::text, 'inference_run'::text, 'submission_draft'::text])))
);

COMMENT ON TABLE public.discord_sync_queue IS 'A pending refresh or deletion of a resource''s Discord posts.

Database triggers insert one row per resource and replace its action and
generation when another change arrives. They also clear any existing claim,
so a worker handling the older generation cannot acknowledge the new request.
Workers read the resource''s current state and delete the row on completion.

The generation is compared with each post''s applied revision. Several changes
can share one pending row; this table does not retain their history.';

COMMENT ON COLUMN public.discord_sync_queue.generation IS 'Sequence number used to compare requested work with a post''s applied revision.

The sequence continues after a queue row is deleted, so the next request does
not restart at a revision the post has already rendered.';

COMMENT ON COLUMN public.discord_sync_queue.available_at IS 'Earliest time a worker may claim this row.

Retries move this time forward without changing enqueued_at, preserving queue
order and the age reported by health metrics.';

COMMENT ON COLUMN public.discord_sync_queue.claim_token IS 'Token generated for the current claim.

Completion and failure updates require a matching token. Enqueuing another
update clears it, preventing an older worker from acknowledging the new work.';

ALTER TABLE ONLY public.discord_sync_queue
    ADD CONSTRAINT discord_sync_queue_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.discord_sync_queue
    ADD CONSTRAINT discord_sync_queue_resource_key UNIQUE (resource_kind, source_key);

CREATE INDEX discord_sync_queue_ready_idx ON public.discord_sync_queue USING btree (available_at) WHERE ((claimed_at IS NULL) AND (dead_at IS NULL));

