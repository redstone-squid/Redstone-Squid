CREATE TABLE public.guild_vote_emojis (
    guild_id bigint NOT NULL,
    kind text NOT NULL,
    identifier text NOT NULL,
    emoji text NOT NULL,
    choice text NOT NULL,
    label text,
    "position" smallint NOT NULL,
    CONSTRAINT guild_vote_emojis_choice_check CHECK ((choice = ANY (ARRAY['approve'::text, 'deny'::text, 'generic'::text]))),
    CONSTRAINT guild_vote_emojis_kind_check CHECK ((kind = ANY (ARRAY['build'::text, 'delete_log'::text, 'generic'::text])))
);

COMMENT ON TABLE public.guild_vote_emojis IS 'One ordered emoji in a guild/session-kind preset.';

ALTER TABLE ONLY public.guild_vote_emojis
    ADD CONSTRAINT guild_vote_emojis_pkey PRIMARY KEY (guild_id, kind, emoji);

ALTER TABLE ONLY public.guild_vote_emojis
    ADD CONSTRAINT guild_vote_emojis_position_key UNIQUE (guild_id, kind, "position");

ALTER TABLE ONLY public.guild_vote_emojis
    ADD CONSTRAINT guild_vote_emojis_guild_id_fkey FOREIGN KEY (guild_id) REFERENCES public.server_settings(server_id) ON DELETE CASCADE;

