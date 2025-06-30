CREATE TABLE
  public.build_restrictions (
    build_id bigint NOT NULL DEFAULT NULL,
    restriction_id smallint NOT NULL DEFAULT NULL
  );

;

CREATE UNIQUE INDEX build_restrictions_pkey ON public.build_restrictions USING btree (build_id, restriction_id);