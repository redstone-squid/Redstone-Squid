CREATE TABLE public.permission_role_assignments (
    id bigint NOT NULL,
    role_id integer NOT NULL,
    granted_by_account_id integer,
    subject_account_id integer,
    subject_role_id bigint,
    subject_guild_id bigint,
    scope_guild_id bigint,
    expires_at timestamp with time zone,
    granted_at timestamp with time zone DEFAULT now() NOT NULL,
    reason text,
    CONSTRAINT permission_role_assignments_one_subject CHECK ((num_nonnulls(subject_account_id, subject_role_id) = 1)),
    CONSTRAINT permission_role_assignments_role_subject_has_guild CHECK (((subject_role_id IS NULL) OR (subject_guild_id IS NOT NULL))),
    CONSTRAINT permission_role_assignments_role_subject_stays_home CHECK (((subject_role_id IS NULL) OR (scope_guild_id IS NULL) OR (scope_guild_id = subject_guild_id)))
);

COMMENT ON TABLE public.permission_role_assignments IS 'A role held by an account or by everyone with a Discord role.';

COMMENT ON COLUMN public.permission_role_assignments.granted_by_account_id IS 'Account that issued the assignment, or None for a system operation.';

ALTER TABLE ONLY public.permission_role_assignments
    ADD CONSTRAINT permission_role_assignments_pkey PRIMARY KEY (id);

CREATE UNIQUE INDEX permission_role_assignments_account_unique ON public.permission_role_assignments USING btree (subject_account_id, role_id, scope_guild_id) NULLS NOT DISTINCT WHERE (subject_account_id IS NOT NULL);

CREATE INDEX permission_role_assignments_by_account ON public.permission_role_assignments USING btree (subject_account_id) WHERE (subject_account_id IS NOT NULL);

CREATE INDEX permission_role_assignments_by_role ON public.permission_role_assignments USING btree (subject_guild_id, subject_role_id) WHERE (subject_role_id IS NOT NULL);

CREATE INDEX permission_role_assignments_granted_by_idx ON public.permission_role_assignments USING btree (granted_by_account_id);

CREATE INDEX permission_role_assignments_role_idx ON public.permission_role_assignments USING btree (role_id);

CREATE UNIQUE INDEX permission_role_assignments_role_unique ON public.permission_role_assignments USING btree (subject_role_id, role_id, scope_guild_id) NULLS NOT DISTINCT WHERE (subject_role_id IS NOT NULL);

CREATE TRIGGER permission_role_assignments_bump_epoch AFTER INSERT OR DELETE OR UPDATE ON public.permission_role_assignments FOR EACH STATEMENT EXECUTE FUNCTION public.bump_permission_epoch();

ALTER TABLE ONLY public.permission_role_assignments
    ADD CONSTRAINT permission_role_assignments_granted_by_fkey FOREIGN KEY (granted_by_account_id) REFERENCES public.accounts(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.permission_role_assignments
    ADD CONSTRAINT permission_role_assignments_role_id_fkey FOREIGN KEY (role_id) REFERENCES public.permission_roles(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.permission_role_assignments
    ADD CONSTRAINT permission_role_assignments_subject_account_fkey FOREIGN KEY (subject_account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;

