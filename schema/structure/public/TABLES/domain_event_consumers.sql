CREATE TABLE public.domain_event_consumers (
    name text NOT NULL
);

COMMENT ON TABLE public.domain_event_consumers IS 'A registered reader of the event log.';

ALTER TABLE ONLY public.domain_event_consumers
    ADD CONSTRAINT domain_event_consumers_pkey PRIMARY KEY (name);

