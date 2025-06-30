CREATE TABLE
  public.votes (
    vote_session_id bigint NOT NULL DEFAULT NULL,
    user_id bigint NOT NULL DEFAULT NULL,
    weight double precision NULL DEFAULT NULL
  );

;

CREATE UNIQUE INDEX votes_pkey ON public.votes USING btree (vote_session_id, user_id);