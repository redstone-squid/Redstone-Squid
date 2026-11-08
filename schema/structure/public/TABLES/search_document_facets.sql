CREATE TABLE public.search_document_facets (
    id bigint NOT NULL,
    document_id bigint NOT NULL,
    field_name text NOT NULL,
    ordinal smallint DEFAULT 0 NOT NULL,
    text_value text,
    numeric_value numeric,
    timestamp_value timestamp with time zone,
    boolean_value boolean,
    CONSTRAINT search_document_facets_one_value_check CHECK ((num_nonnulls(text_value, numeric_value, timestamp_value, boolean_value) = 1))
);

COMMENT ON TABLE public.search_document_facets IS 'A typed, indexed field value belonging to a search document.';

ALTER TABLE ONLY public.search_document_facets
    ADD CONSTRAINT search_document_facets_identity_key UNIQUE (document_id, field_name, ordinal);

ALTER TABLE ONLY public.search_document_facets
    ADD CONSTRAINT search_document_facets_pkey PRIMARY KEY (id);

CREATE INDEX search_document_facets_boolean_idx ON public.search_document_facets USING btree (field_name, boolean_value) WHERE (boolean_value IS NOT NULL);

CREATE INDEX search_document_facets_numeric_idx ON public.search_document_facets USING btree (field_name, numeric_value) WHERE (numeric_value IS NOT NULL);

CREATE INDEX search_document_facets_text_idx ON public.search_document_facets USING btree (field_name, text_value) WHERE (text_value IS NOT NULL);

CREATE INDEX search_document_facets_text_prefix_idx ON public.search_document_facets USING btree (field_name, lower(text_value) text_pattern_ops) WHERE (text_value IS NOT NULL);

CREATE INDEX search_document_facets_timestamp_idx ON public.search_document_facets USING btree (field_name, timestamp_value) WHERE (timestamp_value IS NOT NULL);

ALTER TABLE ONLY public.search_document_facets
    ADD CONSTRAINT search_document_facets_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.search_documents(id) ON DELETE CASCADE;

