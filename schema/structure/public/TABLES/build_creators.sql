CREATE TABLE public.build_creators (
    build_id bigint NOT NULL,
    alias_id integer NOT NULL
);

COMMENT ON TABLE public.build_creators IS 'Association table between builds and the creator names credited on them.';

ALTER TABLE ONLY public.build_creators
    ADD CONSTRAINT build_creators_pkey PRIMARY KEY (build_id, alias_id);

CREATE INDEX build_creators_alias_idx ON public.build_creators USING btree (alias_id);

CREATE TRIGGER build_creators_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.build_creators FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();

CREATE TRIGGER build_creators_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.build_creators FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();

ALTER TABLE ONLY public.build_creators
    ADD CONSTRAINT build_creators_alias_id_fkey FOREIGN KEY (alias_id) REFERENCES public.creator_aliases(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.build_creators
    ADD CONSTRAINT build_creators_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON DELETE CASCADE;

