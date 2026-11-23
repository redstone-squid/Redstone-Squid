"""Store revision-bound proposals for build edit votes.

Revision ID: c7e2a914fd30
Revises: e8fb9e85d696
"""

from alembic import op

revision = "c7e2a914fd30"
down_revision = "e8fb9e85d696"
branch_labels = None
depends_on = None

_KIND_CHECKS = {
    "vote_sessions": "vote_sessions_kind_check",
    "guild_vote_emojis": "guild_vote_emojis_kind_check",
    "guild_vote_role_weights": "guild_vote_role_weights_kind_check",
}
_ORIGINAL_KINDS = "kind IN ('build', 'delete_log', 'generic')"
_EDIT_KINDS = "kind IN ('build', 'build_edit', 'delete_log', 'generic')"

_ORIGINAL_BUILD_CASE = """(session_kind = 'build' AND build_count = 1 AND delete_log_count = 0 AND generic_count = 0)"""
_EDIT_BUILD_CASE = """(session_kind IN ('build', 'build_edit') AND build_count = 1 AND delete_log_count = 0 AND generic_count = 0
                AND (session_kind = 'build_edit') = EXISTS (
                    SELECT 1 FROM public.build_vote_sessions
                    WHERE vote_session_id = target_id AND proposal_revision IS NOT NULL
                ))"""
_SUBTYPE_FUNCTION = """CREATE OR REPLACE FUNCTION public.enforce_vote_session_kind_subtype() RETURNS trigger
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
            (session_kind IN ('build', 'build_edit') AND build_count = 1 AND delete_log_count = 0 AND generic_count = 0
                AND (session_kind = 'build_edit') = EXISTS (
                    SELECT 1 FROM public.build_vote_sessions
                    WHERE vote_session_id = target_id AND proposal_revision IS NOT NULL
                ))
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


def _replace_kind_checks(expression: str) -> None:
    for table, check in _KIND_CHECKS.items():
        op.drop_constraint(check, table, type_="check")
        op.create_check_constraint(check, table, expression)


def upgrade() -> None:
    op.execute("ALTER TABLE edit_operations ADD COLUMN review_vote_id bigint")
    op.execute("ALTER TABLE build_vote_sessions ADD COLUMN proposal_revision bigint, ADD COLUMN proposal_patch jsonb")
    op.create_check_constraint(
        "build_vote_sessions_proposal_check",
        "build_vote_sessions",
        "(proposal_revision IS NULL AND proposal_patch IS NULL) OR "
        "(proposal_revision IS NOT NULL AND proposal_revision > 0 "
        "AND proposal_patch IS NOT NULL AND jsonb_typeof(proposal_patch) = 'object')",
    )
    _replace_kind_checks(_EDIT_KINDS)
    op.execute(_SUBTYPE_FUNCTION)


def downgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM vote_sessions WHERE kind = 'build_edit') THEN
                RAISE EXCEPTION 'Downgrade requires an archive and removal of build edit votes.';
            END IF;
        END
        $$;
    """)
    op.execute(_SUBTYPE_FUNCTION.replace(_EDIT_BUILD_CASE, _ORIGINAL_BUILD_CASE))
    _replace_kind_checks(_ORIGINAL_KINDS)
    op.drop_constraint("build_vote_sessions_proposal_check", "build_vote_sessions", type_="check")
    op.drop_column("build_vote_sessions", "proposal_patch")
    op.drop_column("build_vote_sessions", "proposal_revision")
    op.drop_column("edit_operations", "review_vote_id")
