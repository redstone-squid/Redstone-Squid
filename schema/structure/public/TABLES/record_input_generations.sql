CREATE TABLE public.record_input_generations (
    build_kind text NOT NULL,
    generation bigint DEFAULT 1 NOT NULL,
    CONSTRAINT record_input_generations_generation_check CHECK ((generation >= 0)),
    CONSTRAINT record_input_generations_kind_check CHECK ((build_kind = ANY (ARRAY['door'::text, 'extender'::text])))
);

COMMENT ON TABLE public.record_input_generations IS 'A permanent per-kind counter changed atomically with record invalidation.';

ALTER TABLE ONLY public.record_input_generations
    ADD CONSTRAINT record_input_generations_pkey PRIMARY KEY (build_kind);

