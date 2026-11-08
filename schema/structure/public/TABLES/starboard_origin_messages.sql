CREATE TABLE public.starboard_origin_messages (
    id bigint NOT NULL,
    guild_id bigint NOT NULL,
    channel_id bigint NOT NULL,
    author_id bigint NOT NULL,
    author_is_bot boolean NOT NULL,
    is_nsfw boolean DEFAULT false NOT NULL,
    has_image boolean DEFAULT false NOT NULL,
    posted_at timestamp with time zone NOT NULL,
    seen_at timestamp with time zone DEFAULT now() NOT NULL,
    deleted_at timestamp with time zone
);

COMMENT ON TABLE public.starboard_origin_messages IS 'A source message that has been evaluated by at least one starboard.';

ALTER TABLE ONLY public.starboard_origin_messages
    ADD CONSTRAINT starboard_origin_messages_pkey PRIMARY KEY (id);

CREATE INDEX starboard_origin_messages_guild_idx ON public.starboard_origin_messages USING btree (guild_id);

CREATE TRIGGER starboard_origin_messages_enqueue_discord_sync AFTER UPDATE OF deleted_at ON public.starboard_origin_messages FOR EACH ROW EXECUTE FUNCTION public.enqueue_starboard_sync();

ALTER TABLE ONLY public.starboard_origin_messages
    ADD CONSTRAINT starboard_origin_messages_guild_id_fkey FOREIGN KEY (guild_id) REFERENCES public.server_settings(server_id) ON DELETE CASCADE;

