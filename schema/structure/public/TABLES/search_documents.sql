CREATE TABLE public.search_documents (
    id bigint NOT NULL,
    resource_kind text NOT NULL,
    source_key text NOT NULL,
    title text NOT NULL,
    subtitle text,
    description text,
    status text,
    normalized_title text NOT NULL,
    document_data jsonb DEFAULT '{}'::jsonb NOT NULL,
    source_hash text NOT NULL,
    embedding public.vector(1536),
    embedding_model text,
    refreshed_at timestamp with time zone DEFAULT now() NOT NULL,
    heading_terms text[] DEFAULT '{}'::text[] NOT NULL,
    search_vector tsvector NOT NULL,
    embedding_text text DEFAULT ''::text NOT NULL,
    embedding_hash text NOT NULL
);

COMMENT ON TABLE public.search_documents IS 'A searchable resource: what one search result shows, and the vector that finds it.

Display columns and `document_data` render the hit. Every searchable fact is
a SearchValue row; `search_vector` and `heading_terms` are derived from those
rows when the document is written, for fast recall and ranking.';

COMMENT ON COLUMN public.search_documents.normalized_title IS 'Normalized title and subtitle, for title order and suggestions.';

COMMENT ON COLUMN public.search_documents.source_hash IS 'Digest of the whole document, to skip rewriting it unchanged.';

COMMENT ON COLUMN public.search_documents.heading_terms IS 'Distinct sorted terms of the title and subtitle, to recognize a query that names the heading.';

COMMENT ON COLUMN public.search_documents.embedding_hash IS 'Digest of embedding_text; the embedding is kept while it matches.';

ALTER TABLE ONLY public.search_documents
    ADD CONSTRAINT search_documents_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.search_documents
    ADD CONSTRAINT search_documents_resource_key UNIQUE (resource_kind, source_key);

CREATE INDEX search_documents_embedding_hnsw_idx ON public.search_documents USING hnsw (embedding public.vector_cosine_ops) WHERE (embedding IS NOT NULL);

CREATE INDEX search_documents_scope_idx ON public.search_documents USING btree (resource_kind, status);

CREATE INDEX search_documents_search_vector_idx ON public.search_documents USING gin (search_vector);

CREATE INDEX search_documents_title_trgm_idx ON public.search_documents USING gin (normalized_title public.gin_trgm_ops);

CREATE TRIGGER search_documents_enqueue_dependents_delete AFTER DELETE ON public.search_documents REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.enqueue_dependent_record_search();

CREATE TRIGGER search_documents_enqueue_dependents_insert AFTER INSERT ON public.search_documents REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.enqueue_dependent_record_search();

CREATE TRIGGER search_documents_enqueue_dependents_update AFTER UPDATE ON public.search_documents REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.enqueue_dependent_record_search();

