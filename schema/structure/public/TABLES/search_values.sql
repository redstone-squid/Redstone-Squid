CREATE TABLE public.search_values (
    id bigint NOT NULL,
    document_id bigint NOT NULL,
    field_name text NOT NULL,
    ordinal smallint DEFAULT 0 NOT NULL,
    holder_build_id bigint,
    text_value text,
    normalized_text text,
    term_vector tsvector,
    numeric_value numeric,
    timestamp_value timestamp with time zone,
    boolean_value boolean,
    CONSTRAINT search_values_one_value_check CHECK ((num_nonnulls(text_value, numeric_value, timestamp_value, boolean_value) = 1)),
    CONSTRAINT search_values_text_forms_check CHECK ((((text_value IS NULL) = (normalized_text IS NULL)) AND ((text_value IS NULL) = (term_vector IS NULL))))
);

COMMENT ON TABLE public.search_values IS 'One typed value of a document''s field, such as one of a build''s creators.

Text values keep their original text for display and highlighting, a
normalized form for keyword comparisons, and a vector of their terms for
word and phrase matches scoped to the field. Record holder values carry the
holder''s build ID, so a clause can be matched against a single holder.';

ALTER TABLE ONLY public.search_values
    ADD CONSTRAINT search_values_identity_key UNIQUE (document_id, field_name, ordinal);

ALTER TABLE ONLY public.search_values
    ADD CONSTRAINT search_values_pkey PRIMARY KEY (id);

CREATE INDEX search_values_boolean_idx ON public.search_values USING btree (field_name, boolean_value) WHERE (boolean_value IS NOT NULL);

CREATE INDEX search_values_holder_idx ON public.search_values USING btree (document_id, holder_build_id, field_name) WHERE (holder_build_id IS NOT NULL);

CREATE INDEX search_values_keyword_idx ON public.search_values USING btree (field_name, normalized_text) WHERE (normalized_text IS NOT NULL);

CREATE INDEX search_values_numeric_idx ON public.search_values USING btree (field_name, numeric_value) WHERE (numeric_value IS NOT NULL);

CREATE INDEX search_values_term_vector_idx ON public.search_values USING gin (term_vector);

CREATE INDEX search_values_text_prefix_idx ON public.search_values USING btree (field_name, lower(text_value) text_pattern_ops) WHERE (text_value IS NOT NULL);

CREATE INDEX search_values_timestamp_idx ON public.search_values USING btree (field_name, timestamp_value) WHERE (timestamp_value IS NOT NULL);

ALTER TABLE ONLY public.search_values
    ADD CONSTRAINT search_values_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.search_documents(id) ON DELETE CASCADE;

