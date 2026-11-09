CREATE TABLE public.build_history_states (
    id bigint NOT NULL,
    build_id bigint NOT NULL,
    captured_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    build_revision bigint NOT NULL,
    schema_version smallint NOT NULL,
    snapshot jsonb NOT NULL,
    CONSTRAINT build_history_states_revision_positive CHECK ((build_revision > 0)),
    CONSTRAINT build_history_states_schema_positive CHECK ((schema_version > 0)),
    CONSTRAINT build_history_states_snapshot_object CHECK ((jsonb_typeof(snapshot) = 'object'::text))
);

COMMENT ON TABLE public.build_history_states IS 'One observed build state, shared by adjacent history entries.';

ALTER TABLE ONLY public.build_history_states
    ADD CONSTRAINT build_history_states_build_id_key UNIQUE (build_id, id);

ALTER TABLE ONLY public.build_history_states
    ADD CONSTRAINT build_history_states_pkey PRIMARY KEY (id);

CREATE TRIGGER build_history_states_immutable BEFORE DELETE OR UPDATE OR TRUNCATE ON public.build_history_states FOR EACH STATEMENT EXECUTE FUNCTION public.reject_build_history_mutation();

