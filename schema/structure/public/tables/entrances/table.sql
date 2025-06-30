CREATE TABLE
  public.entrances (build_id bigint NOT NULL DEFAULT NULL);

;

CREATE UNIQUE INDEX entrances_pkey ON public.entrances USING btree (build_id);