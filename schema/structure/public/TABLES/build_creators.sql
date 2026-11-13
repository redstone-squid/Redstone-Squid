CREATE TABLE public.build_creators (
    build_id bigint NOT NULL,
    alias_id integer NOT NULL
);

COMMENT ON TABLE public.build_creators IS 'Association table between builds and the creator names credited on them.';

ALTER TABLE ONLY public.build_creators
    ADD CONSTRAINT build_creators_pkey PRIMARY KEY (build_id, alias_id);

CREATE INDEX build_creators_alias_idx ON public.build_creators USING btree (alias_id);

CREATE TRIGGER build_creators_capture_edit BEFORE INSERT OR DELETE OR UPDATE ON public.build_creators FOR EACH ROW EXECUTE FUNCTION public.capture_build_edit_trigger();

CREATE TRIGGER build_creators_enqueue_build_change AFTER INSERT OR DELETE OR UPDATE ON public.build_creators FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_change();

CREATE TRIGGER build_creators_reject_truncate BEFORE TRUNCATE ON public.build_creators FOR EACH STATEMENT EXECUTE FUNCTION public.capture_build_edit_trigger();

ALTER TABLE ONLY public.build_creators
    ADD CONSTRAINT build_creators_alias_id_fkey FOREIGN KEY (alias_id) REFERENCES public.creator_aliases(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.build_creators
    ADD CONSTRAINT build_creators_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON DELETE CASCADE;

