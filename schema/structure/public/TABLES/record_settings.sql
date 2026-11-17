CREATE TABLE public.record_settings (
    id smallint NOT NULL,
    current_version_id smallint,
    revision bigint DEFAULT 0 NOT NULL,
    CONSTRAINT record_settings_revision_check CHECK ((revision >= 0)),
    CONSTRAINT record_settings_singleton_check CHECK ((id = 1))
);

COMMENT ON TABLE public.record_settings IS 'The shared current Minecraft version, independent of published runs.';

ALTER TABLE ONLY public.record_settings
    ADD CONSTRAINT record_settings_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.record_settings
    ADD CONSTRAINT record_settings_current_version_id_fkey FOREIGN KEY (current_version_id) REFERENCES public.versions(id) ON DELETE RESTRICT;

