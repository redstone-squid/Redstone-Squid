CREATE TABLE public.notification_subscriptions (
    id bigint NOT NULL,
    account_id integer NOT NULL,
    kind text NOT NULL,
    subject_id uuid,
    filter jsonb,
    enabled boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT notification_subscriptions_filter_complete CHECK (((kind = 'record_filter'::text) = (filter IS NOT NULL))),
    CONSTRAINT notification_subscriptions_kind_check CHECK ((kind = ANY (ARRAY['creator'::text, 'record'::text, 'record_filter'::text]))),
    CONSTRAINT notification_subscriptions_subject_complete CHECK (((kind = ANY (ARRAY['creator'::text, 'record'::text])) = (subject_id IS NOT NULL)))
);

COMMENT ON TABLE public.notification_subscriptions IS 'A creator, exact record, or structured record-filter subscription.';

ALTER TABLE ONLY public.notification_subscriptions
    ADD CONSTRAINT notification_subscriptions_pkey PRIMARY KEY (id);

CREATE INDEX notification_subscriptions_account_idx ON public.notification_subscriptions USING btree (account_id, created_at);

CREATE UNIQUE INDEX notification_subscriptions_exact_key ON public.notification_subscriptions USING btree (account_id, kind, subject_id) WHERE (enabled AND (subject_id IS NOT NULL));

CREATE UNIQUE INDEX notification_subscriptions_filter_key ON public.notification_subscriptions USING btree (account_id, kind, filter) WHERE (enabled AND (filter IS NOT NULL));

CREATE INDEX notification_subscriptions_subject_idx ON public.notification_subscriptions USING btree (kind, subject_id);

ALTER TABLE ONLY public.notification_subscriptions
    ADD CONSTRAINT notification_subscriptions_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;

