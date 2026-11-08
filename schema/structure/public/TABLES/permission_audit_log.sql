CREATE TABLE public.permission_audit_log (
    id bigint NOT NULL,
    action text NOT NULL,
    actor_account_id integer,
    subject_kind text,
    subject_id bigint,
    subject_guild_id bigint,
    pattern text,
    role_id integer,
    scope_guild_id bigint,
    effect smallint,
    expires_at timestamp with time zone,
    reason text,
    details jsonb,
    at timestamp with time zone DEFAULT now() NOT NULL
);

COMMENT ON TABLE public.permission_audit_log IS 'An append-only record of one permission mutation.

Written in the same transaction as the change it describes. The repository
exposes no update or delete path for this table.';

COMMENT ON COLUMN public.permission_audit_log.action IS 'Mutation name, such as `grant`, `revoke`, `role_create`, or `role_include`.';

COMMENT ON COLUMN public.permission_audit_log.reason IS 'Reason for the change; the service requires one for a forbid grant.';

ALTER TABLE ONLY public.permission_audit_log
    ADD CONSTRAINT permission_audit_log_pkey PRIMARY KEY (id);

CREATE INDEX permission_audit_log_actor_idx ON public.permission_audit_log USING btree (actor_account_id);

CREATE INDEX permission_audit_log_by_subject ON public.permission_audit_log USING btree (subject_kind, subject_id);

CREATE INDEX permission_audit_log_recent ON public.permission_audit_log USING btree (at);

ALTER TABLE ONLY public.permission_audit_log
    ADD CONSTRAINT permission_audit_log_actor_fkey FOREIGN KEY (actor_account_id) REFERENCES public.accounts(id) ON DELETE SET NULL;

