CREATE TABLE public.permission_grants (
    id bigint NOT NULL,
    pattern text NOT NULL,
    effect smallint NOT NULL,
    granted_by_account_id integer,
    subject_account_id integer,
    subject_role_id bigint,
    subject_guild_id bigint,
    scope_guild_id bigint,
    expires_at timestamp with time zone,
    granted_at timestamp with time zone DEFAULT now() NOT NULL,
    reason text,
    CONSTRAINT permission_grants_effect_check CHECK ((effect = ANY (ARRAY[1, '-1'::integer, '-2'::integer]))),
    CONSTRAINT permission_grants_one_subject CHECK ((num_nonnulls(subject_account_id, subject_role_id) = 1)),
    CONSTRAINT permission_grants_role_subject_has_guild CHECK (((subject_role_id IS NULL) OR (subject_guild_id IS NOT NULL))),
    CONSTRAINT permission_grants_role_subject_stays_home CHECK (((subject_role_id IS NULL) OR (scope_guild_id IS NULL) OR (scope_guild_id = subject_guild_id)))
);

COMMENT ON TABLE public.permission_grants IS 'A direct allow, deny or forbid attached to an account or a Discord role.';

COMMENT ON COLUMN public.permission_grants.granted_by_account_id IS 'Account that issued the grant, or None for a system operation.';

COMMENT ON COLUMN public.permission_grants.subject_role_id IS 'A Discord role snowflake, when the rule is attached to a role.';

COMMENT ON COLUMN public.permission_grants.subject_guild_id IS 'The guild the subject Discord role lives in.';

COMMENT ON COLUMN public.permission_grants.scope_guild_id IS 'Guild where the grant applies, or None for all guilds. Guild-scoped grants cannot satisfy global nodes.';

ALTER TABLE ONLY public.permission_grants
    ADD CONSTRAINT permission_grants_pkey PRIMARY KEY (id);

CREATE UNIQUE INDEX permission_grants_account_unique ON public.permission_grants USING btree (subject_account_id, pattern, scope_guild_id) NULLS NOT DISTINCT WHERE (subject_account_id IS NOT NULL);

CREATE INDEX permission_grants_by_account ON public.permission_grants USING btree (subject_account_id) WHERE (subject_account_id IS NOT NULL);

CREATE INDEX permission_grants_by_role ON public.permission_grants USING btree (subject_guild_id, subject_role_id) WHERE (subject_role_id IS NOT NULL);

CREATE INDEX permission_grants_granted_by_idx ON public.permission_grants USING btree (granted_by_account_id);

CREATE UNIQUE INDEX permission_grants_role_unique ON public.permission_grants USING btree (subject_role_id, pattern, scope_guild_id) NULLS NOT DISTINCT WHERE (subject_role_id IS NOT NULL);

CREATE TRIGGER permission_grants_bump_epoch AFTER INSERT OR DELETE OR UPDATE ON public.permission_grants FOR EACH STATEMENT EXECUTE FUNCTION public.bump_permission_epoch();

ALTER TABLE ONLY public.permission_grants
    ADD CONSTRAINT permission_grants_granted_by_fkey FOREIGN KEY (granted_by_account_id) REFERENCES public.accounts(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.permission_grants
    ADD CONSTRAINT permission_grants_subject_account_fkey FOREIGN KEY (subject_account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;

