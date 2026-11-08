CREATE TABLE public.delete_log_vote_sessions (
    vote_session_id bigint NOT NULL,
    target_message_id bigint NOT NULL,
    target_channel_id bigint NOT NULL,
    target_server_id bigint NOT NULL
);

COMMENT ON TABLE public.delete_log_vote_sessions IS 'Store the Discord message targeted by a deletion vote.';

ALTER TABLE ONLY public.delete_log_vote_sessions
    ADD CONSTRAINT delete_log_vote_sessions_pkey PRIMARY KEY (vote_session_id);

CREATE CONSTRAINT TRIGGER delete_log_vote_sessions_kind_subtype_check AFTER INSERT OR DELETE OR UPDATE ON public.delete_log_vote_sessions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_vote_session_kind_subtype();

ALTER TABLE ONLY public.delete_log_vote_sessions
    ADD CONSTRAINT delete_log_vote_sessions_vote_session_id_fkey FOREIGN KEY (vote_session_id) REFERENCES public.vote_sessions(id) ON UPDATE CASCADE ON DELETE CASCADE;

