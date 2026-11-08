CREATE TABLE public.domain_events (
    id bigint NOT NULL,
    event_type text NOT NULL,
    aggregate_kind text NOT NULL,
    aggregate_id bigint NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    occurred_at timestamp with time zone DEFAULT now() NOT NULL,
    schema_version smallint DEFAULT 1 NOT NULL,
    CONSTRAINT domain_events_schema_version_positive CHECK ((schema_version > 0))
);

COMMENT ON TABLE public.domain_events IS 'One state transition, recorded once and never coalesced.';

ALTER TABLE ONLY public.domain_events
    ADD CONSTRAINT domain_events_pkey PRIMARY KEY (id);

CREATE INDEX domain_events_aggregate_idx ON public.domain_events USING btree (aggregate_kind, aggregate_id);

