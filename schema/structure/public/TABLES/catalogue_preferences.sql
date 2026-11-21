CREATE TABLE public.catalogue_preferences (
    account_id integer NOT NULL,
    scope text NOT NULL,
    filters text NOT NULL,
    sort text NOT NULL,
    CONSTRAINT catalogue_preferences_scope_check CHECK ((scope = ANY (ARRAY['builds'::text, 'records'::text])))
);

COMMENT ON TABLE public.catalogue_preferences IS 'Saved catalogue filters and sort order for an account and tab.';

ALTER TABLE ONLY public.catalogue_preferences
    ADD CONSTRAINT catalogue_preferences_pkey PRIMARY KEY (account_id, scope);

ALTER TABLE ONLY public.catalogue_preferences
    ADD CONSTRAINT catalogue_preferences_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;

