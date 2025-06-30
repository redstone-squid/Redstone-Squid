CREATE TABLE
  public.restrictions (
    id smallint NOT NULL DEFAULT nextval('restrictions_id_seq'::regclass),
    build_category text NULL DEFAULT NULL,
    name text NULL DEFAULT NULL,
    type text NULL DEFAULT NULL
  );

;

CREATE UNIQUE INDEX restrictions_name_key ON public.restrictions USING btree (name);

CREATE UNIQUE INDEX restrictions_pkey ON public.restrictions USING btree (id);