CREATE FUNCTION public.check_permission_role_include_cycle() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    -- Serialize graph checks; the next query gets a fresh READ COMMITTED snapshot.
    -- Repeatable-read snapshots cannot see an edge committed while waiting.
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'role inclusion writes require READ COMMITTED isolation'
            USING ERRCODE = '0A000';
    END IF;
    -- A guild role must not change what a global role grants, and one guild's role must not
    -- change another guild's. Global roles may be included anywhere.
    IF EXISTS (
        SELECT 1
        FROM permission_roles role, permission_roles included
        WHERE role.id = NEW.role_id AND included.id = NEW.included_role_id
          AND included.guild_id IS NOT NULL AND included.guild_id IS DISTINCT FROM role.guild_id
    ) THEN
        RAISE EXCEPTION 'permission role may only include global roles or roles of its own guild'
            USING ERRCODE = '23514', CONSTRAINT = 'permission_role_includes_scope';
    END IF;
    PERFORM pg_advisory_xact_lock(1936812391, 1);
    IF EXISTS (
        WITH RECURSIVE reachable(id) AS (
            SELECT NEW.included_role_id
            UNION
            SELECT edge.included_role_id
            FROM permission_role_includes edge JOIN reachable ON edge.role_id = reachable.id
        )
        SELECT 1 FROM reachable WHERE id = NEW.role_id
    ) THEN
        RAISE EXCEPTION 'permission role inclusion cycle'
            USING ERRCODE = '23514', CONSTRAINT = 'permission_role_includes_acyclic';
    END IF;
    RETURN NEW;
END;
$$;
