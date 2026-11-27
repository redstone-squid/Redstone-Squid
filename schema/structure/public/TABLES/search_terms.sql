CREATE TABLE public.search_terms (
    term text NOT NULL
);

COMMENT ON TABLE public.search_terms IS 'A term that some document has contained, so queries can find close spellings of a word.

Rows are only added. A term no document still contains matches nothing, so it costs
nothing but a row.';

ALTER TABLE ONLY public.search_terms
    ADD CONSTRAINT search_terms_pkey PRIMARY KEY (term);

