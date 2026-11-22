CREATE TABLE public.tag_slugs (
    slug text NOT NULL,
    tag_id bigint,
    CONSTRAINT tag_slugs_format_check CHECK ((slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'::text))
);

COMMENT ON TABLE public.tag_slugs IS 'Reserved tag URL spellings, retained after retirement.';

ALTER TABLE ONLY public.tag_slugs
    ADD CONSTRAINT tag_slugs_owner_key UNIQUE (slug, tag_id);

ALTER TABLE ONLY public.tag_slugs
    ADD CONSTRAINT tag_slugs_pkey PRIMARY KEY (slug);

CREATE INDEX tag_slugs_tag_id_idx ON public.tag_slugs USING btree (tag_id);

ALTER TABLE ONLY public.tag_slugs
    ADD CONSTRAINT tag_slugs_tag_id_fkey FOREIGN KEY (tag_id) REFERENCES public.tag_definitions(id) ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED;

