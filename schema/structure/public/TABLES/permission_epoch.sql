CREATE TABLE public.permission_epoch (
    id smallint NOT NULL,
    version bigint DEFAULT 1 NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT permission_epoch_singleton CHECK ((id = 1))
);

COMMENT ON TABLE public.permission_epoch IS 'A shared counter used to invalidate permission caches.

Database triggers increment the counter and send `NOTIFY squid_permissions`
after permission changes. Watchers poll the counter and use notifications
to refresh sooner.';

ALTER TABLE ONLY public.permission_epoch
    ADD CONSTRAINT permission_epoch_pkey PRIMARY KEY (id);

