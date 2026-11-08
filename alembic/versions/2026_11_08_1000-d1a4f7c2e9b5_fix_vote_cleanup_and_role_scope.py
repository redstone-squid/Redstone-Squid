"""Fix build vote-session cleanup and keep role includes within one guild.

Deleting any build used to delete every vote session without a build or delete-log subtype row,
which includes every generic poll. Cleanup now follows the deleted `build_vote_sessions` row.
"""

from alembic_utils.pg_function import PGFunction
from alembic_utils.pg_trigger import PGTrigger

from alembic import op

revision: str = "d1a4f7c2e9b5"
down_revision: str | None = "c0e4f7a2d5b8"
branch_labels: str | None = None
depends_on: str | None = None

_CLEANUP_FUNCTION = "CREATE FUNCTION public.delete_vote_session_after_build_vote_session_delete() RETURNS trigger\n    LANGUAGE plpgsql\n    AS $$\nBEGIN\n    -- A build session has exactly one subtype row (vote_sessions_kind_subtype_check), so losing\n    -- it, usually through the cascade from a deleted build, leaves a session nothing can show.\n    -- When the delete cascaded from vote_sessions itself, the parent is already gone.\n    DELETE FROM public.vote_sessions WHERE id = OLD.vote_session_id AND kind = 'build';\n    RETURN NULL;\nEND;\n$$;"
_CLEANUP_TRIGGER = "CREATE TRIGGER build_vote_sessions_delete_vote_session AFTER DELETE ON public.build_vote_sessions FOR EACH ROW EXECUTE FUNCTION public.delete_vote_session_after_build_vote_session_delete();"
_OLD_CLEANUP_FUNCTION = "CREATE FUNCTION public.delete_orphaned_build_vote_sessions_after_builds_delete() RETURNS trigger\n    LANGUAGE plpgsql\n    AS $$\nBEGIN\n    DELETE FROM vote_sessions\n    WHERE id IN (\n        SELECT vote_sessions.id\n        FROM vote_sessions vs\n        LEFT JOIN build_vote_sessions bvs ON vs.id = bvs.vote_session_id\n        LEFT JOIN delete_log_vote_sessions dvs ON vs.id = dvs.vote_session_id\n        WHERE bvs.vote_session_id IS NULL AND dvs.vote_session_id IS NULL\n    );\n    RETURN NULL; -- Statement-level triggers do not use OLD or NEW\nEND;\n$$;"
_OLD_CLEANUP_TRIGGER = "CREATE TRIGGER delete_orphaned_build_vote_sessions_after_builds AFTER DELETE ON public.builds FOR EACH STATEMENT EXECUTE FUNCTION public.delete_orphaned_build_vote_sessions_after_builds_delete();"
_INCLUDE_CHECK = "CREATE FUNCTION public.check_permission_role_include_cycle() RETURNS trigger\n    LANGUAGE plpgsql\n    AS $$\nBEGIN\n    -- Serialize graph checks; the next query gets a fresh READ COMMITTED snapshot.\n    -- Repeatable-read snapshots cannot see an edge committed while waiting.\n    IF current_setting('transaction_isolation') <> 'read committed' THEN\n        RAISE EXCEPTION 'role inclusion writes require READ COMMITTED isolation'\n            USING ERRCODE = '0A000';\n    END IF;\n    -- A guild role must not change what a global role grants, and one guild's role must not\n    -- change another guild's. Global roles may be included anywhere.\n    IF EXISTS (\n        SELECT 1\n        FROM permission_roles role, permission_roles included\n        WHERE role.id = NEW.role_id AND included.id = NEW.included_role_id\n          AND included.guild_id IS NOT NULL AND included.guild_id IS DISTINCT FROM role.guild_id\n    ) THEN\n        RAISE EXCEPTION 'permission role may only include global roles or roles of its own guild'\n            USING ERRCODE = '23514', CONSTRAINT = 'permission_role_includes_scope';\n    END IF;\n    PERFORM pg_advisory_xact_lock(1936812391, 1);\n    IF EXISTS (\n        WITH RECURSIVE reachable(id) AS (\n            SELECT NEW.included_role_id\n            UNION\n            SELECT edge.included_role_id\n            FROM permission_role_includes edge JOIN reachable ON edge.role_id = reachable.id\n        )\n        SELECT 1 FROM reachable WHERE id = NEW.role_id\n    ) THEN\n        RAISE EXCEPTION 'permission role inclusion cycle'\n            USING ERRCODE = '23514', CONSTRAINT = 'permission_role_includes_acyclic';\n    END IF;\n    RETURN NEW;\nEND;\n$$;"
_OLD_INCLUDE_CHECK = "CREATE FUNCTION public.check_permission_role_include_cycle() RETURNS trigger\n    LANGUAGE plpgsql\n    AS $$\nBEGIN\n    -- Serialize graph checks; the next query gets a fresh READ COMMITTED snapshot.\n    -- Repeatable-read snapshots cannot see an edge committed while waiting.\n    IF current_setting('transaction_isolation') <> 'read committed' THEN\n        RAISE EXCEPTION 'role inclusion writes require READ COMMITTED isolation'\n            USING ERRCODE = '0A000';\n    END IF;\n    PERFORM pg_advisory_xact_lock(1936812391, 1);\n    IF EXISTS (\n        WITH RECURSIVE reachable(id) AS (\n            SELECT NEW.included_role_id\n            UNION\n            SELECT edge.included_role_id\n            FROM permission_role_includes edge JOIN reachable ON edge.role_id = reachable.id\n        )\n        SELECT 1 FROM reachable WHERE id = NEW.role_id\n    ) THEN\n        RAISE EXCEPTION 'permission role inclusion cycle'\n            USING ERRCODE = '23514', CONSTRAINT = 'permission_role_includes_acyclic';\n    END IF;\n    RETURN NEW;\nEND;\n$$;"


def upgrade() -> None:
    op.drop_entity(PGTrigger.from_sql(_OLD_CLEANUP_TRIGGER))
    op.drop_entity(PGFunction.from_sql(_OLD_CLEANUP_FUNCTION))
    op.create_entity(PGFunction.from_sql(_CLEANUP_FUNCTION))
    op.create_entity(PGTrigger.from_sql(_CLEANUP_TRIGGER))
    # Offline deployment permits dropping includes that let one guild change another's grants.
    op.execute("""
        DELETE FROM permission_role_includes edge
        USING permission_roles role, permission_roles included
        WHERE role.id = edge.role_id AND included.id = edge.included_role_id
          AND included.guild_id IS NOT NULL AND included.guild_id IS DISTINCT FROM role.guild_id
    """)
    op.replace_entity(PGFunction.from_sql(_INCLUDE_CHECK))


def downgrade() -> None:
    op.replace_entity(PGFunction.from_sql(_OLD_INCLUDE_CHECK))
    op.drop_entity(PGTrigger.from_sql(_CLEANUP_TRIGGER))
    op.drop_entity(PGFunction.from_sql(_CLEANUP_FUNCTION))
    op.create_entity(PGFunction.from_sql(_OLD_CLEANUP_FUNCTION))
    op.create_entity(PGTrigger.from_sql(_OLD_CLEANUP_TRIGGER))
