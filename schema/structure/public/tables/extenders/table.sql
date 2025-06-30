CREATE TABLE
  public.extenders (build_id bigint NOT NULL DEFAULT NULL);

;

CREATE UNIQUE INDEX extenders_pkey ON public.extenders USING btree (build_id);