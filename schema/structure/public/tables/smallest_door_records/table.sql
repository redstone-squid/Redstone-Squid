CREATE TABLE
  public.smallest_door_records (
    record_id bigint NOT NULL DEFAULT nextval('smallest_door_records_record_id_seq'::regclass),
    id bigint NOT NULL DEFAULT NULL,
    title text NULL DEFAULT NULL,
    orientation text NOT NULL DEFAULT NULL,
    door_width integer NOT NULL DEFAULT NULL,
    door_height integer NOT NULL DEFAULT NULL,
    door_depth integer NOT NULL DEFAULT 1,
    types text[] NOT NULL DEFAULT NULL,
    restrictions text[] NOT NULL DEFAULT '{}'::text[],
    volume integer NOT NULL DEFAULT NULL,
    restriction_subset text[] NOT NULL DEFAULT NULL
  );

;

CREATE UNIQUE INDEX smallest_door_records_pkey ON public.smallest_door_records USING btree (record_id);

CREATE UNIQUE INDEX smallest_door_records_orientation_door_width_door_height_do_key ON public.smallest_door_records USING btree (
  orientation,
  door_width,
  door_height,
  door_depth,
  types,
  restriction_subset
);

CREATE UNIQUE INDEX unq_smallest_key ON public.smallest_door_records USING btree (
  orientation,
  door_width,
  door_height,
  door_depth,
  types,
  restriction_subset
);

CREATE INDEX idx_smallest_door_records_dims ON public.smallest_door_records USING btree (orientation, door_width, door_height, door_depth);

CREATE INDEX idx_smallest_door_records_types_gin ON public.smallest_door_records USING gin (types);

CREATE INDEX idx_smallest_door_records_restrictions_gin ON public.smallest_door_records USING gin (restrictions);