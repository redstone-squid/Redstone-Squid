CREATE TABLE public.build_vote_sessions (
    vote_session_id bigint NOT NULL,
    build_id bigint NOT NULL,
    changes jsonb NOT NULL
);

COMMENT ON TABLE public.build_vote_sessions IS 'Link a build review to its target and recorded changes.';

ALTER TABLE ONLY public.build_vote_sessions
    ADD CONSTRAINT build_vote_sessions_pkey PRIMARY KEY (vote_session_id);

CREATE INDEX build_vote_sessions_build_idx ON public.build_vote_sessions USING btree (build_id);

CREATE TRIGGER build_vote_sessions_delete_vote_session AFTER DELETE ON public.build_vote_sessions FOR EACH ROW EXECUTE FUNCTION public.delete_vote_session_after_build_vote_session_delete();

CREATE CONSTRAINT TRIGGER build_vote_sessions_kind_subtype_check AFTER INSERT OR DELETE OR UPDATE ON public.build_vote_sessions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_vote_session_kind_subtype();

ALTER TABLE ONLY public.build_vote_sessions
    ADD CONSTRAINT build_vote_sessions_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON UPDATE CASCADE ON DELETE CASCADE;

ALTER TABLE ONLY public.build_vote_sessions
    ADD CONSTRAINT build_vote_sessions_vote_session_id_fkey FOREIGN KEY (vote_session_id) REFERENCES public.vote_sessions(id) ON UPDATE CASCADE ON DELETE CASCADE;

