CREATE TABLE public.notification_deliveries (
    id bigint NOT NULL,
    notification_id bigint NOT NULL,
    account_id integer NOT NULL,
    generation bigint DEFAULT 1 NOT NULL,
    nonce uuid DEFAULT gen_random_uuid() NOT NULL,
    available_at timestamp with time zone DEFAULT now() NOT NULL,
    claimed_at timestamp with time zone,
    claim_token uuid,
    attempts integer DEFAULT 0 NOT NULL,
    sent_at timestamp with time zone,
    dead_at timestamp with time zone,
    last_error text,
    CONSTRAINT notification_deliveries_attempts_nonnegative CHECK ((attempts >= 0)),
    CONSTRAINT notification_deliveries_claim_complete CHECK (((claimed_at IS NULL) = (claim_token IS NULL))),
    CONSTRAINT notification_deliveries_generation_positive CHECK ((generation > 0))
);

COMMENT ON TABLE public.notification_deliveries IS 'A queued Discord DM retained across retries and after successful or terminal delivery.';

ALTER TABLE ONLY public.notification_deliveries
    ADD CONSTRAINT notification_deliveries_notification_id_key UNIQUE (notification_id);

ALTER TABLE ONLY public.notification_deliveries
    ADD CONSTRAINT notification_deliveries_pkey PRIMARY KEY (id);

CREATE INDEX notification_deliveries_account_idx ON public.notification_deliveries USING btree (account_id);

CREATE INDEX notification_deliveries_ready_idx ON public.notification_deliveries USING btree (available_at) WHERE ((claimed_at IS NULL) AND (dead_at IS NULL) AND (sent_at IS NULL));

ALTER TABLE ONLY public.notification_deliveries
    ADD CONSTRAINT notification_deliveries_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.notification_deliveries
    ADD CONSTRAINT notification_deliveries_notification_owner_fkey FOREIGN KEY (notification_id, account_id) REFERENCES public.notifications(id, account_id) ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED;

