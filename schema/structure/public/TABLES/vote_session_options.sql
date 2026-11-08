CREATE TABLE public.vote_session_options (
    vote_session_id bigint NOT NULL,
    emoji text NOT NULL,
    choice text NOT NULL,
    multiplier double precision DEFAULT 1.0 NOT NULL,
    "position" smallint NOT NULL,
    identifier text NOT NULL,
    guild_id bigint,
    label text,
    id bigint NOT NULL,
    CONSTRAINT vote_session_options_choice_check CHECK ((choice = ANY (ARRAY['approve'::text, 'deny'::text, 'generic'::text]))),
    CONSTRAINT vote_session_options_multiplier_check CHECK (((multiplier > (0)::double precision) AND (multiplier <> 'Infinity'::double precision) AND (multiplier <> 'NaN'::double precision))),
    CONSTRAINT vote_session_options_position_check CHECK (("position" >= 0))
);

COMMENT ON TABLE public.vote_session_options IS 'Ordered reaction options and positive weight multipliers captured for each vote session.';

COMMENT ON COLUMN public.vote_session_options.guild_id IS 'Guild using this emoji alias, or None for an option shared across guilds.';

ALTER TABLE ONLY public.vote_session_options
    ADD CONSTRAINT vote_session_options_alias_key UNIQUE NULLS NOT DISTINCT (vote_session_id, guild_id, emoji);

ALTER TABLE ONLY public.vote_session_options
    ADD CONSTRAINT vote_session_options_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.vote_session_options
    ADD CONSTRAINT vote_session_options_session_id_key UNIQUE (vote_session_id, id);

ALTER TABLE ONLY public.vote_session_options
    ADD CONSTRAINT vote_session_options_vote_session_id_position_key UNIQUE NULLS NOT DISTINCT (vote_session_id, guild_id, "position");

ALTER TABLE ONLY public.vote_session_options
    ADD CONSTRAINT vote_session_options_vote_session_id_fkey FOREIGN KEY (vote_session_id) REFERENCES public.vote_sessions(id) ON UPDATE CASCADE ON DELETE CASCADE;

