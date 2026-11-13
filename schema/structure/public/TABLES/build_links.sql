CREATE TABLE public.build_links (
    build_id bigint NOT NULL,
    url text NOT NULL,
    media_type text
);

COMMENT ON TABLE public.build_links IS 'A URL associated with a build, such as an image, schematic, or world download.';

ALTER TABLE ONLY public.build_links
    ADD CONSTRAINT build_links_pkey PRIMARY KEY (build_id, url);

CREATE TRIGGER build_links_capture_edit BEFORE INSERT OR DELETE OR UPDATE ON public.build_links FOR EACH ROW EXECUTE FUNCTION public.capture_build_edit_trigger();

CREATE TRIGGER build_links_enqueue_build_change AFTER INSERT OR DELETE OR UPDATE ON public.build_links FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_change();

CREATE TRIGGER build_links_reject_truncate BEFORE TRUNCATE ON public.build_links FOR EACH STATEMENT EXECUTE FUNCTION public.capture_build_edit_trigger();

ALTER TABLE ONLY public.build_links
    ADD CONSTRAINT build_links_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON DELETE CASCADE;

