CREATE TABLE public.runtime_setting_changes (
    id bigint NOT NULL,
    actor_account_id integer,
    changed_at timestamp with time zone DEFAULT now() NOT NULL,
    before jsonb NOT NULL,
    after jsonb NOT NULL
);

COMMENT ON TABLE public.runtime_setting_changes IS 'An administrator''s committed change, including before and after snapshots.';

COMMENT ON COLUMN public.runtime_setting_changes.actor_account_id IS 'Internal account that made the edit; retained as null if that account is deleted.';

ALTER TABLE ONLY public.runtime_setting_changes ALTER COLUMN id SET DEFAULT nextval('public.runtime_setting_changes_id_seq'::regclass);

ALTER TABLE ONLY public.runtime_setting_changes
    ADD CONSTRAINT runtime_setting_changes_pkey PRIMARY KEY (id);

CREATE INDEX ix_runtime_setting_changes_actor_account_id ON public.runtime_setting_changes USING btree (actor_account_id);

ALTER TABLE ONLY public.runtime_setting_changes
    ADD CONSTRAINT runtime_setting_changes_actor_account_id_fkey FOREIGN KEY (actor_account_id) REFERENCES public.accounts(id) ON DELETE SET NULL;

