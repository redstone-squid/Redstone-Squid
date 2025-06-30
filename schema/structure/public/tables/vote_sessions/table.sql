CREATE TABLE
  public.vote_sessions (
    id bigint NOT NULL DEFAULT NULL,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    status text NOT NULL DEFAULT NULL,
    author_id bigint NOT NULL DEFAULT NULL,
    kind text NOT NULL DEFAULT NULL,
    fail_threshold smallint NOT NULL DEFAULT NULL,
    pass_threshold smallint NOT NULL DEFAULT NULL
  );

;

CREATE UNIQUE INDEX vote_sessions_pkey ON public.vote_sessions USING btree (id);