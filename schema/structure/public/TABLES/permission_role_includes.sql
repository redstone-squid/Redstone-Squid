CREATE TABLE public.permission_role_includes (
    role_id integer NOT NULL,
    included_role_id integer NOT NULL,
    added_by_account_id integer,
    added_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT permission_role_includes_no_self_include CHECK ((role_id <> included_role_id))
);

COMMENT ON TABLE public.permission_role_includes IS 'A composition edge: one role including another''s patterns.';

ALTER TABLE ONLY public.permission_role_includes
    ADD CONSTRAINT permission_role_includes_pkey PRIMARY KEY (role_id, included_role_id);

CREATE INDEX permission_role_includes_added_by_idx ON public.permission_role_includes USING btree (added_by_account_id);

CREATE INDEX permission_role_includes_included_idx ON public.permission_role_includes USING btree (included_role_id);

CREATE CONSTRAINT TRIGGER permission_role_includes_acyclic AFTER INSERT OR UPDATE ON public.permission_role_includes NOT DEFERRABLE INITIALLY IMMEDIATE FOR EACH ROW EXECUTE FUNCTION public.check_permission_role_include_cycle();

CREATE TRIGGER permission_role_includes_bump_epoch BEFORE INSERT OR DELETE OR UPDATE ON public.permission_role_includes FOR EACH STATEMENT EXECUTE FUNCTION public.bump_permission_epoch();

ALTER TABLE ONLY public.permission_role_includes
    ADD CONSTRAINT permission_role_includes_added_by_fkey FOREIGN KEY (added_by_account_id) REFERENCES public.accounts(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.permission_role_includes
    ADD CONSTRAINT permission_role_includes_included_role_id_fkey FOREIGN KEY (included_role_id) REFERENCES public.permission_roles(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.permission_role_includes
    ADD CONSTRAINT permission_role_includes_role_id_fkey FOREIGN KEY (role_id) REFERENCES public.permission_roles(id) ON DELETE CASCADE;

