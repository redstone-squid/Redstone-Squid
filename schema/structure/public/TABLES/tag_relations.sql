CREATE TABLE public.tag_relations (
    source_tag_id bigint NOT NULL,
    relation_kind text NOT NULL,
    target_tag_id bigint NOT NULL,
    CONSTRAINT tag_relations_distinct_check CHECK ((source_tag_id <> target_tag_id)),
    CONSTRAINT tag_relations_kind_check CHECK ((relation_kind = ANY (ARRAY['implies'::text, 'incompatible'::text])))
);

COMMENT ON TABLE public.tag_relations IS 'A semantic relationship between official restrictions.';

ALTER TABLE ONLY public.tag_relations
    ADD CONSTRAINT tag_relations_pkey PRIMARY KEY (source_tag_id, relation_kind, target_tag_id);

CREATE INDEX tag_relations_target_idx ON public.tag_relations USING btree (target_tag_id);

ALTER TABLE ONLY public.tag_relations
    ADD CONSTRAINT tag_relations_source_fkey FOREIGN KEY (source_tag_id) REFERENCES public.tag_definitions(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.tag_relations
    ADD CONSTRAINT tag_relations_target_fkey FOREIGN KEY (target_tag_id) REFERENCES public.tag_definitions(id) ON DELETE CASCADE;

