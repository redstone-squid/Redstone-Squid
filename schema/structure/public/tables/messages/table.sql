CREATE TABLE
  public.messages (
    server_id bigint NOT NULL DEFAULT NULL,
    build_id bigint NULL DEFAULT NULL,
    channel_id bigint NULL DEFAULT NULL,
    id bigint NOT NULL DEFAULT NULL,
    updated_at timestamp with time zone NULL DEFAULT NULL,
    purpose text NOT NULL DEFAULT NULL,
    content text NULL DEFAULT NULL,
    author_id bigint NOT NULL DEFAULT NULL,
    vote_session_id bigint NULL DEFAULT NULL
  );

;

CREATE UNIQUE INDEX messages_pkey ON public.messages USING btree (id);