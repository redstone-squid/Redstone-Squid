CREATE TABLE public.title_definition_facets (
    definition_id bigint NOT NULL,
    facet_kind text NOT NULL,
    facet_id bigint NOT NULL,
    display_order smallint DEFAULT 0 NOT NULL,
    CONSTRAINT title_definition_facets_kind_check CHECK ((facet_kind = 'restriction'::text))
);

COMMENT ON TABLE public.title_definition_facets IS 'A canonical taxonomy facet belonging to a record definition.';

ALTER TABLE ONLY public.title_definition_facets
    ADD CONSTRAINT title_definition_facets_pkey PRIMARY KEY (definition_id, facet_kind, facet_id);

CREATE INDEX title_definition_facets_lookup_idx ON public.title_definition_facets USING btree (facet_kind, facet_id);

ALTER TABLE ONLY public.title_definition_facets
    ADD CONSTRAINT title_definition_facets_definition_id_fkey FOREIGN KEY (definition_id) REFERENCES public.title_definitions(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.title_definition_facets
    ADD CONSTRAINT title_definition_facets_facet_id_fkey FOREIGN KEY (facet_id) REFERENCES public.tag_definitions(id) ON DELETE CASCADE;

