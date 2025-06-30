CREATE TABLE
  public.utilities (build_id bigint NOT NULL DEFAULT NULL);

;

CREATE UNIQUE INDEX utilities_pkey ON public.utilities USING btree (build_id);