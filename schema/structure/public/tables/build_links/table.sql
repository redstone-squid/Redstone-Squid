CREATE TABLE
  public.build_links (
    build_id bigint NOT NULL DEFAULT NULL,
    url text NOT NULL DEFAULT NULL,
    media_type text NULL DEFAULT NULL
  );

;

CREATE UNIQUE INDEX build_links_pkey ON public.build_links USING btree (build_id, url);