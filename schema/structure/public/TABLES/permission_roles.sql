CREATE TABLE public.permission_roles (
    id integer NOT NULL,
    slug text NOT NULL,
    name text NOT NULL,
    description text,
    guild_id bigint,
    builtin_key text,
    rank integer DEFAULT 0 NOT NULL,
    protected boolean DEFAULT false NOT NULL,
    created_by_account_id integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    withdrawn boolean DEFAULT false NOT NULL,
    CONSTRAINT permission_roles_builtin_is_global CHECK (((builtin_key IS NULL) OR (guild_id IS NULL))),
    CONSTRAINT permission_roles_slug_format CHECK ((slug ~ '^[a-z][a-z0-9-]{1,31}$'::text))
);

COMMENT ON TABLE public.permission_roles IS 'A named bundle of permission patterns and management settings.

Built-in pattern lists live in code. Stored patterns supplement those lists,
while this row supplies the assignment target, rank, and display metadata.';

COMMENT ON COLUMN public.permission_roles.slug IS 'Stable handle used in commands, unique within the owning guild.';

COMMENT ON COLUMN public.permission_roles.name IS 'Display name.';

COMMENT ON COLUMN public.permission_roles.guild_id IS 'The guild owning this role, or NULL for a global one.';

COMMENT ON COLUMN public.permission_roles.builtin_key IS 'Identifies a role whose patterns are defined in code rather than here.';

COMMENT ON COLUMN public.permission_roles.rank IS 'Management rank used to decide who may edit the role. It does not affect permission resolution.';

COMMENT ON COLUMN public.permission_roles.protected IS 'Refuses structural edits from anyone but the bot owner.';

ALTER TABLE ONLY public.permission_roles
    ADD CONSTRAINT permission_roles_builtin_key_key UNIQUE (builtin_key);

ALTER TABLE ONLY public.permission_roles
    ADD CONSTRAINT permission_roles_guild_slug_key UNIQUE NULLS NOT DISTINCT (guild_id, slug);

ALTER TABLE ONLY public.permission_roles
    ADD CONSTRAINT permission_roles_pkey PRIMARY KEY (id);

CREATE INDEX permission_roles_created_by_idx ON public.permission_roles USING btree (created_by_account_id);

CREATE TRIGGER permission_roles_bump_epoch BEFORE INSERT OR DELETE OR UPDATE ON public.permission_roles FOR EACH STATEMENT EXECUTE FUNCTION public.bump_permission_epoch();

ALTER TABLE ONLY public.permission_roles
    ADD CONSTRAINT permission_roles_created_by_fkey FOREIGN KEY (created_by_account_id) REFERENCES public.accounts(id) ON DELETE SET NULL;

