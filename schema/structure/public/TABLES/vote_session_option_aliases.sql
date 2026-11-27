CREATE TABLE public.vote_session_option_aliases (
    id bigint NOT NULL,
    vote_session_id bigint NOT NULL,
    option_identifier text NOT NULL,
    guild_id bigint,
    emoji text NOT NULL,
    "position" smallint NOT NULL,
    CONSTRAINT vote_session_option_aliases_position_check CHECK (("position" >= 0))
);

COMMENT ON TABLE public.vote_session_option_aliases IS 'An emoji that selects a session option on one guild''s cards, or every guild''s when guild_id is None.';

ALTER TABLE ONLY public.vote_session_option_aliases
    ADD CONSTRAINT vote_session_option_aliases_emoji_key UNIQUE NULLS NOT DISTINCT (vote_session_id, guild_id, emoji);

ALTER TABLE ONLY public.vote_session_option_aliases
    ADD CONSTRAINT vote_session_option_aliases_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.vote_session_option_aliases
    ADD CONSTRAINT vote_session_option_aliases_position_key UNIQUE NULLS NOT DISTINCT (vote_session_id, guild_id, "position");

ALTER TABLE ONLY public.vote_session_option_aliases
    ADD CONSTRAINT vote_session_option_aliases_option_fkey FOREIGN KEY (vote_session_id, option_identifier) REFERENCES public.vote_session_options(vote_session_id, identifier) ON UPDATE CASCADE ON DELETE CASCADE;

