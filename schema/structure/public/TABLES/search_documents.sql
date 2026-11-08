CREATE TABLE public.search_documents (
    id bigint NOT NULL,
    resource_kind text NOT NULL,
    source_key text NOT NULL,
    title text NOT NULL,
    subtitle text,
    description text,
    status text,
    normalized_title text NOT NULL,
    fuzzy_text text NOT NULL,
    tags text[] DEFAULT '{}'::text[] NOT NULL,
    title_vector tsvector,
    description_vector tsvector,
    combined_vector tsvector,
    document_data jsonb DEFAULT '{}'::jsonb NOT NULL,
    source_hash text NOT NULL,
    embedding public.vector(1536),
    embedding_model text,
    refreshed_at timestamp with time zone DEFAULT now() NOT NULL
);

COMMENT ON TABLE public.search_documents IS 'An indexed projection of a searchable application resource.';

ALTER TABLE ONLY public.search_documents
    ADD CONSTRAINT search_documents_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.search_documents
    ADD CONSTRAINT search_documents_resource_key UNIQUE (resource_kind, source_key);

CREATE INDEX search_documents_combined_fts_idx ON public.search_documents USING gin (combined_vector);

CREATE INDEX search_documents_description_fts_idx ON public.search_documents USING gin (description_vector);

CREATE INDEX search_documents_embedding_hnsw_idx ON public.search_documents USING hnsw (embedding public.vector_cosine_ops) WHERE (embedding IS NOT NULL);

CREATE INDEX search_documents_fuzzy_trgm_idx ON public.search_documents USING gin (fuzzy_text public.gin_trgm_ops);

CREATE INDEX search_documents_scope_idx ON public.search_documents USING btree (resource_kind, status);

CREATE INDEX search_documents_tags_idx ON public.search_documents USING gin (tags);

CREATE INDEX search_documents_title_fts_idx ON public.search_documents USING gin (title_vector);

