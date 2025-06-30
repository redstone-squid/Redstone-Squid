CREATE TABLE
  public.doors (
    build_id bigint NOT NULL DEFAULT NULL,
    orientation text NOT NULL DEFAULT NULL,
    door_width integer NOT NULL DEFAULT NULL,
    door_height integer NOT NULL DEFAULT NULL,
    door_depth integer NULL DEFAULT NULL,
    normal_opening_time bigint NULL DEFAULT NULL,
    normal_closing_time bigint NULL DEFAULT NULL,
    visible_opening_time bigint NULL DEFAULT NULL,
    visible_closing_time bigint NULL DEFAULT NULL
  );

;

CREATE UNIQUE INDEX doors_pkey ON public.doors USING btree (build_id);