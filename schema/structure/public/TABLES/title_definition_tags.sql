CREATE TABLE public.title_definition_tags (
    definition_id bigint NOT NULL,
    tag_role text NOT NULL,
    tag_id bigint NOT NULL,
    display_order smallint DEFAULT 0 NOT NULL,
    CONSTRAINT title_definition_tags_kind_check CHECK ((tag_role = 'restriction'::text))
);

COMMENT ON TABLE public.title_definition_tags IS 'A tag required by a record definition.';

ALTER TABLE ONLY public.title_definition_tags
    ADD CONSTRAINT title_definition_tags_pkey PRIMARY KEY (definition_id, tag_role, tag_id);

CREATE INDEX title_definition_tags_lookup_idx ON public.title_definition_tags USING btree (tag_role, tag_id);

ALTER TABLE ONLY public.title_definition_tags
    ADD CONSTRAINT title_definition_tags_definition_id_fkey FOREIGN KEY (definition_id) REFERENCES public.title_definitions(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.title_definition_tags
    ADD CONSTRAINT title_definition_tags_tag_id_fkey FOREIGN KEY (tag_id) REFERENCES public.tag_definitions(id) ON DELETE CASCADE;

