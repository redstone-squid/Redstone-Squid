CREATE TABLE public.server_settings (
    server_id bigint NOT NULL,
    smallest_channel_id bigint,
    fastest_channel_id bigint,
    first_channel_id bigint,
    builds_channel_id bigint,
    voting_channel_id bigint,
    in_server boolean DEFAULT true NOT NULL,
    locale text
);

COMMENT ON TABLE public.server_settings IS 'Settings for a Discord server.';

COMMENT ON COLUMN public.server_settings.locale IS 'Admin-configured language override, e.g. "en" or "zh-CN". Falls back to Discord''s guild/user locale when unset.';

ALTER TABLE ONLY public.server_settings
    ADD CONSTRAINT server_settings_fastest_channel_id_key UNIQUE (fastest_channel_id);

ALTER TABLE ONLY public.server_settings
    ADD CONSTRAINT server_settings_first_channel_id_key UNIQUE (first_channel_id);

ALTER TABLE ONLY public.server_settings
    ADD CONSTRAINT server_settings_pkey PRIMARY KEY (server_id);

ALTER TABLE ONLY public.server_settings
    ADD CONSTRAINT server_settings_smallest_channel_id_key UNIQUE (smallest_channel_id);

