CREATE TABLE
  public.users (
    id integer NOT NULL DEFAULT nextval('users_id_seq'::regclass),
    discord_id bigint NULL DEFAULT NULL,
    minecraft_uuid uuid NULL DEFAULT NULL,
    ign text NULL DEFAULT NULL,
    created_at timestamp without time zone NULL DEFAULT now()
  );

;

CREATE UNIQUE INDEX users_pkey ON public.users USING btree (id);