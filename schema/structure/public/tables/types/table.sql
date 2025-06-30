CREATE TABLE
  public.types (
    id smallint NOT NULL DEFAULT nextval('types_id_seq'::regclass),
    build_category text NULL DEFAULT NULL,
    name text NULL DEFAULT NULL
  );

;

CREATE UNIQUE INDEX types_name_key ON public.types USING btree (name);

CREATE UNIQUE INDEX types_pkey ON public.types USING btree (id);