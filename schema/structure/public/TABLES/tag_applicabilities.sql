CREATE TABLE public.tag_applicabilities (
    tag_id bigint NOT NULL,
    build_kind text NOT NULL
);

COMMENT ON TABLE public.tag_applicabilities IS 'A build kind on which a tag may be used.';

ALTER TABLE ONLY public.tag_applicabilities
    ADD CONSTRAINT tag_applicabilities_pkey PRIMARY KEY (tag_id, build_kind);

ALTER TABLE ONLY public.tag_applicabilities
    ADD CONSTRAINT tag_applicabilities_tag_id_fkey FOREIGN KEY (tag_id) REFERENCES public.tag_definitions(id) ON DELETE CASCADE;

