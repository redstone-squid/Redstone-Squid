CREATE TABLE public.record_input_snapshots (
    id bigint NOT NULL,
    document jsonb NOT NULL,
    CONSTRAINT record_input_snapshots_version_check CHECK ((((jsonb_typeof(document) = 'object'::text) AND ((document -> 'schema_version'::text) = '1'::jsonb)) IS TRUE))
);

COMMENT ON TABLE public.record_input_snapshots IS 'Immutable concrete inputs saved before arithmetic starts.';

ALTER TABLE ONLY public.record_input_snapshots
    ADD CONSTRAINT record_input_snapshots_pkey PRIMARY KEY (id);

CREATE TRIGGER record_input_snapshots_immutable BEFORE DELETE OR UPDATE ON public.record_input_snapshots FOR EACH ROW EXECUTE FUNCTION public.reject_record_input_snapshot_mutation();

