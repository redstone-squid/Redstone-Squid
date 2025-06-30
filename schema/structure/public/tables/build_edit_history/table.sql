CREATE TABLE
  public.build_edit_history (
    build_id bigint NOT NULL DEFAULT NULL,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    version smallint NOT NULL DEFAULT NULL
  );

;

CREATE UNIQUE INDEX build_edit_history_pkey ON public.build_edit_history USING btree (build_id);

CREATE UNIQUE INDEX unique_version_per_build ON public.build_edit_history USING btree (build_id, version);