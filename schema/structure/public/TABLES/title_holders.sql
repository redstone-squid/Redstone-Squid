CREATE TABLE public.title_holders (
    result_id bigint NOT NULL,
    build_id bigint NOT NULL,
    rank smallint DEFAULT 1 NOT NULL,
    metric_snapshot jsonb NOT NULL,
    title text NOT NULL,
    subtitle text,
    completion_at timestamp with time zone,
    CONSTRAINT title_holders_rank_check CHECK ((rank > 0))
);

COMMENT ON TABLE public.title_holders IS 'A co-holder of a resolved computed record.';

ALTER TABLE ONLY public.title_holders
    ADD CONSTRAINT title_holders_pkey PRIMARY KEY (result_id, build_id);

CREATE INDEX title_holders_build_idx ON public.title_holders USING btree (build_id);

CREATE TRIGGER title_holders_enqueue_search_delete AFTER DELETE ON public.title_holders REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.enqueue_record_search_projection();

CREATE TRIGGER title_holders_enqueue_search_insert AFTER INSERT ON public.title_holders REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.enqueue_record_search_projection();

CREATE TRIGGER title_holders_enqueue_search_update AFTER UPDATE ON public.title_holders REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.enqueue_record_search_projection();

ALTER TABLE ONLY public.title_holders
    ADD CONSTRAINT title_holders_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.title_holders
    ADD CONSTRAINT title_holders_result_id_fkey FOREIGN KEY (result_id) REFERENCES public.title_results(id) ON DELETE CASCADE;

