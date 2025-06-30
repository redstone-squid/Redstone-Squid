CREATE TABLE
  public.build_types (
    build_id bigint NOT NULL DEFAULT NULL,
    type_id smallint NOT NULL DEFAULT NULL
  );

;

CREATE UNIQUE INDEX build_types_pkey ON public.build_types USING btree (build_id, type_id);