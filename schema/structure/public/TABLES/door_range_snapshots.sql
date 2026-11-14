CREATE TABLE public.door_range_snapshots (
    run_id bigint NOT NULL,
    document jsonb NOT NULL,
    content_hash text NOT NULL,
    CONSTRAINT door_range_snapshots_version_check CHECK ((((jsonb_typeof(document) = 'object'::text) AND ((document -> 'schema_version'::text) = '1'::jsonb)) IS TRUE))
);

COMMENT ON TABLE public.door_range_snapshots IS 'Immutable arithmetic facts captured before a range worker starts.';

ALTER TABLE ONLY public.door_range_snapshots
    ADD CONSTRAINT door_range_snapshots_pkey PRIMARY KEY (run_id);

CREATE TRIGGER door_range_snapshots_immutable BEFORE DELETE OR UPDATE ON public.door_range_snapshots FOR EACH ROW EXECUTE FUNCTION public.reject_door_range_snapshot_mutation();

ALTER TABLE ONLY public.door_range_snapshots
    ADD CONSTRAINT door_range_snapshots_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.door_range_runs(id) ON DELETE RESTRICT;

