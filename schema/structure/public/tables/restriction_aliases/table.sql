CREATE TABLE
  public.restriction_aliases (
    restriction_id smallint NOT NULL DEFAULT NULL,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    alias text NOT NULL DEFAULT NULL
  );

;

CREATE UNIQUE INDEX restriction_aliases_pkey ON public.restriction_aliases USING btree (alias);

CREATE INDEX restriction_aliases_restriction_id_idx ON public.restriction_aliases USING btree (restriction_id);