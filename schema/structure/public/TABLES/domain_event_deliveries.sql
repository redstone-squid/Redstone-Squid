CREATE TABLE public.domain_event_deliveries (
    event_id bigint NOT NULL,
    consumer text NOT NULL,
    available_at timestamp with time zone DEFAULT now() NOT NULL,
    claimed_at timestamp with time zone,
    attempts integer DEFAULT 0 NOT NULL,
    last_error text,
    dead_at timestamp with time zone,
    claim_token uuid,
    CONSTRAINT domain_event_deliveries_attempts_nonnegative CHECK ((attempts >= 0)),
    CONSTRAINT domain_event_deliveries_claim_complete CHECK (((claimed_at IS NULL) = (claim_token IS NULL)))
);

COMMENT ON TABLE public.domain_event_deliveries IS 'One consumer''s outstanding delivery of one event.';

ALTER TABLE ONLY public.domain_event_deliveries
    ADD CONSTRAINT domain_event_deliveries_pkey PRIMARY KEY (event_id, consumer);

CREATE INDEX domain_event_deliveries_ready_idx ON public.domain_event_deliveries USING btree (available_at) WHERE ((claimed_at IS NULL) AND (dead_at IS NULL));

CREATE TRIGGER domain_event_deliveries_work_activity AFTER INSERT OR DELETE OR UPDATE ON public.domain_event_deliveries FOR EACH ROW EXECUTE FUNCTION public.record_work_activity('queue', 'event_id', 'consumer');

ALTER TABLE ONLY public.domain_event_deliveries
    ADD CONSTRAINT domain_event_deliveries_consumer_fkey FOREIGN KEY (consumer) REFERENCES public.domain_event_consumers(name) ON DELETE CASCADE;

ALTER TABLE ONLY public.domain_event_deliveries
    ADD CONSTRAINT domain_event_deliveries_event_id_fkey FOREIGN KEY (event_id) REFERENCES public.domain_events(id) ON DELETE CASCADE;

