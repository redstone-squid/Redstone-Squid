"""Close schema compatibility drift.

Revision ID: d9f3b6c1e4a7
Revises: c8e2a5b0d3f6
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d9f3b6c1e4a7"
down_revision: str | Sequence[str] | None = "c8e2a5b0d3f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PRINCIPAL_COMMENT = "Rolling-deploy mirror removed after the caller-vocabulary compatibility window."
_OLD_PRINCIPAL_COMMENT = """The caller namespace a key is reserved in.

The application layer calls this the *caller*; the column keeps the older
word because renaming it needs a migration, a rewrite of the unique index it
anchors, and a redeploy window, for a name no client ever sees. The same
trade applies to the `principal` partition in `RateLimit-Policy` and to
`SQUID_API_RATE_LIMIT_PRINCIPAL_REQUESTS`, both of which deployments and
clients can observe. If the ban is meant repo-wide, that is its own commit."""

_TABLE_COMMENTS = {
    "media_artifacts": (
        "Content-addressed normalized output, video thumbnail, or report metadata.",
        "Content-addressed normalized output, poster, or disclosure report metadata.",
    ),
    "submission_finalization_inputs": (
        "Original normalized input retained independently of mutable execution state.",
        "Original normalized request retained independently of mutable finalization execution state.",
    ),
    "submission_finalization_results": (
        "Immutable build identity retained after successful finalization.",
        "Immutable build identity and target provenance retained after success.",
    ),
}

_SCHEMATIC_PREVIEW_COMMENT = "Durable upload and cleanup state for one generated preview object."

_MIRROR_FUNCTION = """
CREATE FUNCTION idempotency_requests_mirror_caller() RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.caller IS NULL THEN
        NEW.caller := NEW.principal;
    ELSIF NEW.principal IS NULL THEN
        NEW.principal := NEW.caller;
    ELSIF NEW.caller <> NEW.principal THEN
        RAISE EXCEPTION 'idempotency caller vocabulary columns disagree'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$
"""


def upgrade() -> None:
    """Retire a redundant rollout trigger and synchronize schema documentation."""
    op.execute("DROP TRIGGER idempotency_requests_mirror_caller ON idempotency_requests")
    op.execute("DROP FUNCTION idempotency_requests_mirror_caller()")
    op.alter_column(
        "idempotency_requests",
        "principal",
        comment=_PRINCIPAL_COMMENT,
        existing_comment=_OLD_PRINCIPAL_COMMENT,
    )
    for table, (new_comment, old_comment) in _TABLE_COMMENTS.items():
        op.create_table_comment(table, new_comment, existing_comment=old_comment)
    op.create_table_comment("schematic_preview_objects", _SCHEMATIC_PREVIEW_COMMENT, existing_comment=None)


def downgrade() -> None:
    """Restore the rollout trigger and prior comments without changing data."""
    op.drop_table_comment("schematic_preview_objects", existing_comment=_SCHEMATIC_PREVIEW_COMMENT)
    for table, (new_comment, old_comment) in reversed(_TABLE_COMMENTS.items()):
        op.create_table_comment(table, old_comment, existing_comment=new_comment)
    op.alter_column(
        "idempotency_requests",
        "principal",
        comment=_OLD_PRINCIPAL_COMMENT,
        existing_comment=_PRINCIPAL_COMMENT,
    )
    op.execute(_MIRROR_FUNCTION)
    op.execute(
        """
        CREATE TRIGGER idempotency_requests_mirror_caller
        BEFORE INSERT OR UPDATE OF caller, principal ON idempotency_requests
        FOR EACH ROW EXECUTE FUNCTION idempotency_requests_mirror_caller()
        """
    )
