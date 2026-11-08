CREATE TABLE public.starboard_sources (
    starboard_id bigint NOT NULL,
    guild_id bigint NOT NULL,
    channel_id bigint DEFAULT 0 NOT NULL,
    approved_by bigint,
    approved_at timestamp with time zone
);

COMMENT ON TABLE public.starboard_sources IS 'A guild or channel whose messages feed a starboard.';

COMMENT ON COLUMN public.starboard_sources.channel_id IS 'Source channel ID, or zero to include every channel in the source guild.';

ALTER TABLE ONLY public.starboard_sources
    ADD CONSTRAINT starboard_sources_pkey PRIMARY KEY (starboard_id, guild_id, channel_id);

CREATE INDEX starboard_sources_guild_idx ON public.starboard_sources USING btree (guild_id);

ALTER TABLE ONLY public.starboard_sources
    ADD CONSTRAINT starboard_sources_guild_id_fkey FOREIGN KEY (guild_id) REFERENCES public.server_settings(server_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.starboard_sources
    ADD CONSTRAINT starboard_sources_starboard_id_fkey FOREIGN KEY (starboard_id) REFERENCES public.starboards(id) ON DELETE CASCADE;

