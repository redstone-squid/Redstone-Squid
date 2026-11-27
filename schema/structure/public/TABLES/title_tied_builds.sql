CREATE TABLE public.title_tied_builds (
    result_id bigint NOT NULL,
    build_id bigint NOT NULL
);

COMMENT ON TABLE public.title_tied_builds IS 'A build that matches a record''s holders on every metric but was completed later.';

ALTER TABLE ONLY public.title_tied_builds
    ADD CONSTRAINT title_tied_builds_pkey PRIMARY KEY (result_id, build_id);

CREATE INDEX title_tied_builds_build_idx ON public.title_tied_builds USING btree (build_id);

CREATE TRIGGER title_tied_builds_enqueue_search_delete AFTER DELETE ON public.title_tied_builds REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.enqueue_record_search_projection();

CREATE TRIGGER title_tied_builds_enqueue_search_insert AFTER INSERT ON public.title_tied_builds REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.enqueue_record_search_projection();

CREATE TRIGGER title_tied_builds_enqueue_search_update AFTER UPDATE ON public.title_tied_builds REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.enqueue_record_search_projection();

ALTER TABLE ONLY public.title_tied_builds
    ADD CONSTRAINT title_tied_builds_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.title_tied_builds
    ADD CONSTRAINT title_tied_builds_result_id_fkey FOREIGN KEY (result_id) REFERENCES public.title_results(id) ON DELETE CASCADE;

