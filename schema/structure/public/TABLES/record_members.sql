CREATE TABLE public.record_members (
    title_id uuid NOT NULL,
    build_id bigint NOT NULL
);

COMMENT ON TABLE public.record_members IS 'A build in a live record''s category, whether or not it competes.

There is no foreign key to builds: a deleted build''s rows must survive until
a worker recomputes the records it leaves.';

ALTER TABLE ONLY public.record_members
    ADD CONSTRAINT record_members_pkey PRIMARY KEY (title_id, build_id);

CREATE INDEX record_members_build_idx ON public.record_members USING btree (build_id);

ALTER TABLE ONLY public.record_members
    ADD CONSTRAINT record_members_title_id_fkey FOREIGN KEY (title_id) REFERENCES public.record_titles(public_id) ON DELETE CASCADE;

