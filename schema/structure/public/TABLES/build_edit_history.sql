CREATE TABLE public.build_edit_history (
    id bigint NOT NULL,
    build_id bigint NOT NULL,
    recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    operation_id uuid NOT NULL,
    operation text NOT NULL,
    source text NOT NULL,
    actor_account_id integer,
    actor_system text,
    correlation_kind text,
    correlation_id text,
    before_state_id bigint,
    after_state_id bigint,
    CONSTRAINT build_history_account_positive CHECK (((actor_account_id IS NULL) OR (actor_account_id > 0))),
    CONSTRAINT build_history_actor_check CHECK ((num_nonnulls(actor_account_id, actor_system) = 1)),
    CONSTRAINT build_history_correlation_check CHECK (((correlation_kind IS NULL) = (correlation_id IS NULL))),
    CONSTRAINT build_history_source_check CHECK (((length(btrim(source)) > 0) AND ((actor_system IS NULL) OR (length(btrim(actor_system)) > 0)))),
    CONSTRAINT build_history_states_check CHECK ((((operation = ANY (ARRAY['baseline'::text, 'create'::text])) AND (before_state_id IS NULL) AND (after_state_id IS NOT NULL)) OR ((operation = 'delete'::text) AND (before_state_id IS NOT NULL) AND (after_state_id IS NULL)) OR ((operation = ANY (ARRAY['edit'::text, 'moderation'::text, 'recalculation'::text, 'schematic'::text, 'account_merge'::text])) AND (before_state_id IS NOT NULL) AND (after_state_id IS NOT NULL))))
);

COMMENT ON TABLE public.build_edit_history IS 'An append-only operation whose states and actor survive live-record deletion.';

ALTER TABLE ONLY public.build_edit_history
    ADD CONSTRAINT build_edit_history_build_id_key UNIQUE (build_id, operation_id);

ALTER TABLE ONLY public.build_edit_history
    ADD CONSTRAINT build_edit_history_pkey PRIMARY KEY (id);

CREATE INDEX build_edit_history_timeline_idx ON public.build_edit_history USING btree (build_id, id);

CREATE TRIGGER build_edit_history_immutable BEFORE DELETE OR UPDATE OR TRUNCATE ON public.build_edit_history FOR EACH STATEMENT EXECUTE FUNCTION public.reject_build_history_mutation();

ALTER TABLE ONLY public.build_edit_history
    ADD CONSTRAINT build_history_after_fkey FOREIGN KEY (build_id, after_state_id) REFERENCES public.build_history_states(build_id, id);

ALTER TABLE ONLY public.build_edit_history
    ADD CONSTRAINT build_history_before_fkey FOREIGN KEY (build_id, before_state_id) REFERENCES public.build_history_states(build_id, id);

