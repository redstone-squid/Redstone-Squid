CREATE TABLE
  public.verification_codes (
    id smallint NOT NULL DEFAULT nextval('verification_codes_id_seq'::regclass),
    minecraft_uuid uuid NOT NULL DEFAULT NULL,
    code text NOT NULL DEFAULT NULL,
    created timestamp without time zone NOT NULL DEFAULT now(),
    expires timestamp without time zone NOT NULL DEFAULT (now() + '00:10:00'::interval),
    username text NOT NULL DEFAULT NULL,
    valid boolean NOT NULL DEFAULT true
  );

;

CREATE UNIQUE INDEX verification_codes_pkey ON public.verification_codes USING btree (id);