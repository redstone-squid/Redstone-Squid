CREATE TABLE
  public.build_creators (
    build_id bigint NOT NULL DEFAULT NULL,
    user_id integer NOT NULL DEFAULT NULL
  );

;

CREATE UNIQUE INDEX build_creators_pkey ON public.build_creators USING btree (build_id, user_id);