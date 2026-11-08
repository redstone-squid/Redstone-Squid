CREATE TABLE public.generic_vote_sessions (
    vote_session_id bigint NOT NULL,
    guild_id bigint,
    question text NOT NULL,
    visibility text NOT NULL,
    deadline timestamp with time zone NOT NULL,
    scope text DEFAULT 'guild'::text NOT NULL,
    CONSTRAINT generic_vote_sessions_network_guild_check CHECK (((scope = 'guild'::text) OR (guild_id IS NOT NULL))),
    CONSTRAINT generic_vote_sessions_scope_check CHECK ((scope = ANY (ARRAY['guild'::text, 'network'::text]))),
    CONSTRAINT generic_vote_sessions_visibility_check CHECK ((visibility = ANY (ARRAY['anonymous_live'::text, 'visible_live'::text, 'anonymous_hidden'::text])))
);

COMMENT ON TABLE public.generic_vote_sessions IS 'Metadata for a user-created generic poll.';

COMMENT ON COLUMN public.generic_vote_sessions.guild_id IS 'Owning guild used for emoji configuration and ballot role weights.

Guild polls may be created without one; network polls require an owner.
Presentation messages are attached separately.';

COMMENT ON COLUMN public.generic_vote_sessions.scope IS 'Whether publication is limited to the owning guild or spans configured vote channels.';

ALTER TABLE ONLY public.generic_vote_sessions
    ADD CONSTRAINT generic_vote_sessions_pkey PRIMARY KEY (vote_session_id);

CREATE INDEX generic_vote_sessions_deadline_idx ON public.generic_vote_sessions USING btree (deadline);

CREATE INDEX generic_vote_sessions_guild_idx ON public.generic_vote_sessions USING btree (guild_id);

CREATE CONSTRAINT TRIGGER generic_vote_sessions_kind_subtype_check AFTER INSERT OR DELETE OR UPDATE ON public.generic_vote_sessions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_vote_session_kind_subtype();

ALTER TABLE ONLY public.generic_vote_sessions
    ADD CONSTRAINT generic_vote_sessions_guild_id_fkey FOREIGN KEY (guild_id) REFERENCES public.server_settings(server_id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.generic_vote_sessions
    ADD CONSTRAINT generic_vote_sessions_vote_session_id_fkey FOREIGN KEY (vote_session_id) REFERENCES public.vote_sessions(id) ON UPDATE CASCADE ON DELETE CASCADE;

