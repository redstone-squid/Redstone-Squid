CREATE TABLE
  public.delete_log_vote_sessions (
    vote_session_id bigint NOT NULL DEFAULT NULL,
    target_message_id bigint NOT NULL DEFAULT NULL,
    target_channel_id bigint NOT NULL DEFAULT NULL,
    target_server_id bigint NOT NULL DEFAULT NULL
  );

;

CREATE UNIQUE INDEX delete_log_vote_sessions_pkey ON public.delete_log_vote_sessions USING btree (vote_session_id);