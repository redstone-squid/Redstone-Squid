CREATE TABLE public.permission_role_patterns (
    role_id integer NOT NULL,
    pattern text NOT NULL,
    mode smallint NOT NULL,
    added_by_account_id integer,
    added_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT permission_role_patterns_mode_check CHECK ((mode = ANY (ARRAY[1, '-1'::integer])))
);

COMMENT ON TABLE public.permission_role_patterns IS 'One included or excluded pattern in a role.

Exclusions remove matching nodes from this role''s contribution without denying
those nodes when another role or direct rule grants them.';

COMMENT ON COLUMN public.permission_role_patterns.pattern IS 'A node name, a `*`/`**` wildcard, or an `@tag` selector.';

COMMENT ON COLUMN public.permission_role_patterns.mode IS '1 to include or -1 to exclude; a role stores one mode for each pattern.';

ALTER TABLE ONLY public.permission_role_patterns
    ADD CONSTRAINT permission_role_patterns_pkey PRIMARY KEY (role_id, pattern);

CREATE INDEX permission_role_patterns_added_by_idx ON public.permission_role_patterns USING btree (added_by_account_id);

CREATE TRIGGER permission_role_patterns_bump_epoch BEFORE INSERT OR DELETE OR UPDATE ON public.permission_role_patterns FOR EACH STATEMENT EXECUTE FUNCTION public.bump_permission_epoch();

ALTER TABLE ONLY public.permission_role_patterns
    ADD CONSTRAINT permission_role_patterns_added_by_fkey FOREIGN KEY (added_by_account_id) REFERENCES public.accounts(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.permission_role_patterns
    ADD CONSTRAINT permission_role_patterns_role_id_fkey FOREIGN KEY (role_id) REFERENCES public.permission_roles(id) ON DELETE CASCADE;

