CREATE TABLE public.title_holder_history (
    id bigint NOT NULL,
    definition_id bigint NOT NULL,
    build_id bigint NOT NULL,
    predecessor_id bigint,
    held_from timestamp with time zone NOT NULL,
    held_until timestamp with time zone,
    metric_snapshot jsonb NOT NULL,
    title_id uuid NOT NULL,
    CONSTRAINT title_holder_history_interval_check CHECK (((held_until IS NULL) OR (held_until >= held_from)))
);

COMMENT ON TABLE public.title_holder_history IS 'A reconstructed interval in a record''s beaten-record chronology.

Each publication replaces a record''s periods. Retirement keeps them and
closes the open one.';

ALTER TABLE ONLY public.title_holder_history
    ADD CONSTRAINT title_holder_history_identity_key UNIQUE (title_id, build_id, held_from);

ALTER TABLE ONLY public.title_holder_history
    ADD CONSTRAINT title_holder_history_pkey PRIMARY KEY (id);

CREATE INDEX title_holder_history_build_idx ON public.title_holder_history USING btree (build_id);

CREATE INDEX title_holder_history_definition_idx ON public.title_holder_history USING btree (definition_id);

ALTER TABLE ONLY public.title_holder_history
    ADD CONSTRAINT title_holder_history_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.title_holder_history
    ADD CONSTRAINT title_holder_history_definition_id_fkey FOREIGN KEY (definition_id) REFERENCES public.title_definitions(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.title_holder_history
    ADD CONSTRAINT title_holder_history_predecessor_id_fkey FOREIGN KEY (predecessor_id) REFERENCES public.title_holder_history(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.title_holder_history
    ADD CONSTRAINT title_holder_history_title_id_fkey FOREIGN KEY (title_id) REFERENCES public.record_titles(public_id) ON DELETE CASCADE;

