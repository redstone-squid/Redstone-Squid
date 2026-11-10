CREATE TABLE public.runtime_settings (
    id integer NOT NULL,
    welcome_forward_chance double precision NOT NULL,
    welcome_enabled boolean NOT NULL,
    redstoner_enabled boolean NOT NULL,
    build_vote_pass_threshold smallint NOT NULL,
    build_vote_fail_threshold smallint NOT NULL,
    delete_vote_pass_threshold smallint NOT NULL,
    delete_vote_fail_threshold smallint NOT NULL,
    notification_retention_days integer NOT NULL,
    redstoner_starboard_author_id bigint NOT NULL,
    redstoner_starboard_channel_id bigint NOT NULL,
    redstoner_role_id bigint NOT NULL,
    redstoner_corner_channel_id bigint NOT NULL,
    redstoner_announcement_channel_id bigint NOT NULL,
    welcome_channel_id bigint NOT NULL,
    welcome_relay_channel_id bigint NOT NULL,
    version_tracker_channel_id bigint NOT NULL,
    build_log_channel_ids bigint[] NOT NULL,
    CONSTRAINT runtime_settings_chance CHECK (((welcome_forward_chance >= (0)::double precision) AND (welcome_forward_chance <= (1)::double precision))),
    CONSTRAINT runtime_settings_retention CHECK (((notification_retention_days >= 1) AND (notification_retention_days <= 3650))),
    CONSTRAINT runtime_settings_singleton CHECK ((id = 1)),
    CONSTRAINT runtime_settings_thresholds CHECK (((build_vote_pass_threshold > 0) AND (build_vote_fail_threshold < 0) AND (delete_vote_pass_threshold > 0) AND (delete_vote_fail_threshold < 0)))
);

COMMENT ON TABLE public.runtime_settings IS 'Singleton containing global defaults and owner-guild community routing.';

ALTER TABLE ONLY public.runtime_settings
    ADD CONSTRAINT runtime_settings_pkey PRIMARY KEY (id);

