"""Enforce vote-session kind and subtype coherence

Each vote session is an aggregate split across a root table and exactly one of
three subtype tables. Foreign keys keep subtype rows attached to a root, but
they cannot require the root kind to agree or require exactly one payload.

A deferred constraint trigger checks the final state at commit, allowing the
repository to insert or transition the two rows atomically. The migration first
rejects pre-existing incoherent aggregates rather than installing an invariant
that silently ignores them.

Revision ID: a9b5c8d3e6f0
Revises: f8a4c7d2b5e9
Create Date: 2026-08-19 11:00:00.000000+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a9b5c8d3e6f0"
down_revision: str | Sequence[str] | None = "f8a4c7d2b5e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FUNCTION = """
CREATE FUNCTION public.enforce_vote_session_kind_subtype() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    target_ids bigint[];
    target_id bigint;
    session_kind text;
    build_count integer;
    delete_log_count integer;
    generic_count integer;
BEGIN
    IF TG_TABLE_NAME = 'vote_sessions' THEN
        target_ids := ARRAY[CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END];
        IF TG_OP = 'UPDATE' AND OLD.id IS DISTINCT FROM NEW.id THEN
            target_ids := array_append(target_ids, OLD.id);
        END IF;
    ELSE
        target_ids := ARRAY[
            CASE WHEN TG_OP = 'DELETE' THEN OLD.vote_session_id ELSE NEW.vote_session_id END
        ];
        IF TG_OP = 'UPDATE' AND OLD.vote_session_id IS DISTINCT FROM NEW.vote_session_id THEN
            target_ids := array_append(target_ids, OLD.vote_session_id);
        END IF;
    END IF;

    FOREACH target_id IN ARRAY target_ids LOOP
        -- The foreign keys take KEY SHARE locks while inserting subtype rows. NO KEY
        -- UPDATE stays compatible with those locks while serializing the final checks
        -- of two transactions that target the same vote session.
        SELECT kind
        INTO session_kind
        FROM public.vote_sessions
        WHERE id = target_id
        FOR NO KEY UPDATE;

        IF NOT FOUND THEN
            CONTINUE;
        END IF;

        SELECT
            (SELECT count(*) FROM public.build_vote_sessions WHERE vote_session_id = target_id),
            (SELECT count(*) FROM public.delete_log_vote_sessions WHERE vote_session_id = target_id),
            (SELECT count(*) FROM public.generic_vote_sessions WHERE vote_session_id = target_id)
        INTO build_count, delete_log_count, generic_count;

        IF NOT (
            (session_kind = 'build' AND build_count = 1 AND delete_log_count = 0 AND generic_count = 0)
            OR (session_kind = 'delete_log' AND build_count = 0 AND delete_log_count = 1 AND generic_count = 0)
            OR (session_kind = 'generic' AND build_count = 0 AND delete_log_count = 0 AND generic_count = 1)
        ) THEN
            RAISE EXCEPTION USING
                ERRCODE = 'check_violation',
                MESSAGE = format(
                    'vote session %s kind %s has subtype counts build=%s, delete_log=%s, generic=%s',
                    target_id,
                    session_kind,
                    build_count,
                    delete_log_count,
                    generic_count
                ),
                SCHEMA = 'public',
                TABLE = 'vote_sessions',
                CONSTRAINT = 'vote_sessions_kind_subtype_check';
        END IF;
    END LOOP;

    RETURN NULL;
END;
$$;
"""

_TRIGGERS = (
    "CREATE CONSTRAINT TRIGGER vote_sessions_kind_subtype_check "
    "AFTER INSERT OR DELETE OR UPDATE ON public.vote_sessions "
    "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
    "EXECUTE FUNCTION public.enforce_vote_session_kind_subtype()",
    "CREATE CONSTRAINT TRIGGER build_vote_sessions_kind_subtype_check "
    "AFTER INSERT OR DELETE OR UPDATE ON public.build_vote_sessions "
    "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
    "EXECUTE FUNCTION public.enforce_vote_session_kind_subtype()",
    "CREATE CONSTRAINT TRIGGER delete_log_vote_sessions_kind_subtype_check "
    "AFTER INSERT OR DELETE OR UPDATE ON public.delete_log_vote_sessions "
    "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
    "EXECUTE FUNCTION public.enforce_vote_session_kind_subtype()",
    "CREATE CONSTRAINT TRIGGER generic_vote_sessions_kind_subtype_check "
    "AFTER INSERT OR DELETE OR UPDATE ON public.generic_vote_sessions "
    "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
    "EXECUTE FUNCTION public.enforce_vote_session_kind_subtype()",
)

_VALIDATE = """
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM public.vote_sessions AS sessions
        WHERE NOT (
            (
                sessions.kind = 'build'
                AND (SELECT count(*) FROM public.build_vote_sessions WHERE vote_session_id = sessions.id) = 1
                AND NOT EXISTS (
                    SELECT 1 FROM public.delete_log_vote_sessions WHERE vote_session_id = sessions.id
                )
                AND NOT EXISTS (
                    SELECT 1 FROM public.generic_vote_sessions WHERE vote_session_id = sessions.id
                )
            )
            OR (
                sessions.kind = 'delete_log'
                AND NOT EXISTS (
                    SELECT 1 FROM public.build_vote_sessions WHERE vote_session_id = sessions.id
                )
                AND (SELECT count(*) FROM public.delete_log_vote_sessions WHERE vote_session_id = sessions.id) = 1
                AND NOT EXISTS (
                    SELECT 1 FROM public.generic_vote_sessions WHERE vote_session_id = sessions.id
                )
            )
            OR (
                sessions.kind = 'generic'
                AND NOT EXISTS (
                    SELECT 1 FROM public.build_vote_sessions WHERE vote_session_id = sessions.id
                )
                AND NOT EXISTS (
                    SELECT 1 FROM public.delete_log_vote_sessions WHERE vote_session_id = sessions.id
                )
                AND (SELECT count(*) FROM public.generic_vote_sessions WHERE vote_session_id = sessions.id) = 1
            )
        )
    ) THEN
        RAISE EXCEPTION
            'cannot enforce vote session subtype kinds while incoherent aggregates exist';
    END IF;
END;
$$;
"""


def upgrade() -> None:
    """Reject existing drift, then install the deferred aggregate invariant."""
    op.execute(_VALIDATE)
    op.execute(_FUNCTION)
    for trigger in _TRIGGERS:
        op.execute(trigger)


def downgrade() -> None:
    """Remove the deferred aggregate invariant."""
    for table, trigger in (
        ("generic_vote_sessions", "generic_vote_sessions_kind_subtype_check"),
        ("delete_log_vote_sessions", "delete_log_vote_sessions_kind_subtype_check"),
        ("build_vote_sessions", "build_vote_sessions_kind_subtype_check"),
        ("vote_sessions", "vote_sessions_kind_subtype_check"),
    ):
        op.execute(f"DROP TRIGGER {trigger} ON public.{table}")
    op.execute("DROP FUNCTION public.enforce_vote_session_kind_subtype()")
