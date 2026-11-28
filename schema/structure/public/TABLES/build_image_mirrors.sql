CREATE TABLE public.build_image_mirrors (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    build_id bigint NOT NULL,
    url text NOT NULL,
    attempts integer DEFAULT 1 NOT NULL,
    retry_at timestamp with time zone DEFAULT (now() + '00:10:00'::interval) NOT NULL,
    public_url text,
    content bytea,
    content_type text
);

COMMENT ON TABLE public.build_image_mirrors IS 'A bounded copy of an image retained for the lifetime of its source link.';

ALTER TABLE ONLY public.build_image_mirrors
    ADD CONSTRAINT build_image_mirrors_build_id_url_key UNIQUE (build_id, url);

ALTER TABLE ONLY public.build_image_mirrors
    ADD CONSTRAINT build_image_mirrors_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.build_image_mirrors
    ADD CONSTRAINT build_image_mirrors_build_id_url_fkey FOREIGN KEY (build_id, url) REFERENCES public.build_links(build_id, url) ON DELETE CASCADE;

