CREATE TABLE public.messages (
    guild_id bigint,
    channel_id bigint,
    id bigint NOT NULL,
    content text,
    author_id bigint NOT NULL,
    created_at timestamp with time zone,
    observed_at timestamp with time zone DEFAULT now() NOT NULL,
    edited_at timestamp with time zone,
    deleted_at timestamp with time zone
);

COMMENT ON TABLE public.messages IS 'A Discord message the bot has seen.

One row per Discord message, holding only what is true about the message itself.
Why it matters is expressed by the tables that reference it: `discord_posts` for
the messages the bot owns and renders, `build_source_messages` for the ones a
build was inferred from.';

COMMENT ON COLUMN public.messages.guild_id IS 'The guild the message was sent in, or NULL in DMs.';

COMMENT ON COLUMN public.messages.id IS 'The Discord snowflake. Never generated here; the message exists before the row.';

COMMENT ON COLUMN public.messages.content IS 'Never exposed through the API. Retained for offline build inference and edit views.';

COMMENT ON COLUMN public.messages.created_at IS 'When Discord created the message, denormalised from the snowflake.';

COMMENT ON COLUMN public.messages.observed_at IS 'When the bot first recorded this message.';

COMMENT ON COLUMN public.messages.edited_at IS 'When `content` was last refreshed from a Discord edit.';

COMMENT ON COLUMN public.messages.deleted_at IS 'When Discord reported deletion. The deletion handler retains this row and its content.';

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_pkey PRIMARY KEY (id);

CREATE INDEX messages_guild_idx ON public.messages USING btree (guild_id);

CREATE TRIGGER messages_enqueue_starboard_sync AFTER UPDATE OF deleted_at ON public.messages FOR EACH ROW WHEN ((old.deleted_at IS DISTINCT FROM new.deleted_at)) EXECUTE FUNCTION public.enqueue_starboard_sync();

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT public_messages_server_id_fkey FOREIGN KEY (guild_id) REFERENCES public.server_settings(server_id) ON DELETE RESTRICT;

