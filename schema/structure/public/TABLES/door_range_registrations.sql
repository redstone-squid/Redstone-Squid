CREATE TABLE public.door_range_registrations (
    id bigint NOT NULL,
    query_key text NOT NULL,
    query jsonb NOT NULL,
    generation bigint DEFAULT 0 NOT NULL,
    last_checked_at timestamp with time zone,
    CONSTRAINT door_range_registrations_generation_check CHECK ((generation >= 0))
);

COMMENT ON TABLE public.door_range_registrations IS 'A registered range query and its latest reserved computation generation.';

ALTER TABLE ONLY public.door_range_registrations
    ADD CONSTRAINT door_range_registrations_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.door_range_registrations
    ADD CONSTRAINT door_range_registrations_query_key_key UNIQUE (query_key);

