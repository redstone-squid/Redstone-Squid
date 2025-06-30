CREATE TABLE
  public.build_vote_sessions (
    vote_session_id bigint NOT NULL DEFAULT NULL,
    build_id bigint NOT NULL DEFAULT NULL,
    changes jsonb NOT NULL DEFAULT NULL
  );

;

CREATE UNIQUE INDEX build_vote_sessions_pkey ON public.build_vote_sessions USING btree (vote_session_id);