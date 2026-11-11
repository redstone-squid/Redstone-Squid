CREATE TABLE public.notifications (
    id bigint NOT NULL,
    account_id integer NOT NULL,
    event_id bigint NOT NULL,
    source_key text NOT NULL,
    kind text NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    web_visible boolean NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    read_at timestamp with time zone,
    revision bigint DEFAULT 1 NOT NULL,
    CONSTRAINT notifications_kind_check CHECK ((kind = ANY (ARRAY['build_confirmed'::text, 'build_denied'::text, 'creator_build_confirmed'::text, 'record_gained'::text, 'staff_build_submitted'::text]))),
    CONSTRAINT notifications_revision_positive CHECK ((revision > 0))
);

COMMENT ON TABLE public.notifications IS 'An event-derived message for one recipient, shared by the web inbox and DM delivery.';

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_id_account_key UNIQUE (id, account_id);

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_source_key_key UNIQUE (source_key);

CREATE INDEX notifications_account_inbox_idx ON public.notifications USING btree (account_id, id);

CREATE INDEX notifications_created_idx ON public.notifications USING btree (created_at);

CREATE INDEX notifications_event_idx ON public.notifications USING btree (event_id);

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_event_id_fkey FOREIGN KEY (event_id) REFERENCES public.domain_events(id) ON DELETE CASCADE;

