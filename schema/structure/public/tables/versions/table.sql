CREATE TABLE
  public.versions (
    id smallint NOT NULL DEFAULT nextval('versions_id_seq'::regclass),
    edition text NOT NULL DEFAULT NULL,
    major_version smallint NOT NULL DEFAULT NULL,
    minor_version smallint NOT NULL DEFAULT NULL,
    patch_number smallint NOT NULL DEFAULT NULL
  );

;

CREATE UNIQUE INDEX versions_pkey ON public.versions USING btree (id);