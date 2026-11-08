CREATE TABLE public.notification_profiles (
    account_id integer NOT NULL,
    web_enabled boolean DEFAULT false NOT NULL,
    dm_enabled boolean DEFAULT false NOT NULL,
    dm_suspended_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

COMMENT ON TABLE public.notification_profiles IS 'Channel preferences for an account. Consent is recorded on the account itself.';

ALTER TABLE ONLY public.notification_profiles
    ADD CONSTRAINT notification_profiles_pkey PRIMARY KEY (account_id);

ALTER TABLE ONLY public.notification_profiles
    ADD CONSTRAINT notification_profiles_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;

