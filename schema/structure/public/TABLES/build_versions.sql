CREATE TABLE public.build_versions (
    build_id bigint NOT NULL,
    version_id smallint NOT NULL
);

COMMENT ON TABLE public.build_versions IS 'Association table between builds and their versions.';

ALTER TABLE ONLY public.build_versions
    ADD CONSTRAINT build_versions_pkey PRIMARY KEY (build_id, version_id);

CREATE INDEX build_versions_version_idx ON public.build_versions USING btree (version_id);

CREATE TRIGGER build_versions_capture_edit BEFORE INSERT OR DELETE OR UPDATE ON public.build_versions FOR EACH ROW EXECUTE FUNCTION public.capture_build_edit_trigger();

CREATE TRIGGER build_versions_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.build_versions FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();

CREATE TRIGGER build_versions_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.build_versions FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();

CREATE TRIGGER build_versions_reject_truncate BEFORE TRUNCATE ON public.build_versions FOR EACH STATEMENT EXECUTE FUNCTION public.capture_build_edit_trigger();

ALTER TABLE ONLY public.build_versions
    ADD CONSTRAINT build_versions_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.build_versions
    ADD CONSTRAINT build_versions_version_id_fkey FOREIGN KEY (version_id) REFERENCES public.versions(id) ON DELETE RESTRICT;

