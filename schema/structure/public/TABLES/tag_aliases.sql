CREATE TABLE public.tag_aliases (
    tag_id bigint NOT NULL,
    alias text NOT NULL,
    normalized_alias text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

COMMENT ON TABLE public.tag_aliases IS 'An alternate display name for a tag.';

ALTER TABLE ONLY public.tag_aliases
    ADD CONSTRAINT tag_aliases_pkey PRIMARY KEY (tag_id, normalized_alias);

CREATE INDEX tag_aliases_normalized_idx ON public.tag_aliases USING btree (normalized_alias);

CREATE TRIGGER tag_aliases_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.tag_aliases FOR EACH ROW EXECUTE FUNCTION public.enqueue_metadata_search_projection();

ALTER TABLE ONLY public.tag_aliases
    ADD CONSTRAINT tag_aliases_tag_id_fkey FOREIGN KEY (tag_id) REFERENCES public.tag_definitions(id) ON DELETE CASCADE;

