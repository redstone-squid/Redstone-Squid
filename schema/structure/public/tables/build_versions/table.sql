CREATE TABLE
  public.build_versions (
    build_id bigint NOT NULL DEFAULT NULL,
    version_id smallint NOT NULL DEFAULT NULL
  );

;

CREATE UNIQUE INDEX build_versions_pkey ON public.build_versions USING btree (build_id, version_id);