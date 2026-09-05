"""Expand idempotency persistence to caller vocabulary.

Revision ID: b0c6d9e4f7a1
Revises: a9b5c8d3e6f0
Create Date: 2026-08-31 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b0c6d9e4f7a1"
down_revision: str | Sequence[str] | None = "a9b5c8d3e6f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

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
    """Add a dual-written caller column without breaking older API binaries."""
    op.add_column(
        "idempotency_requests",
        sa.Column(
            "caller",
            sa.Text(),
            nullable=True,
            comment="The server-derived caller namespace a key is reserved in.",
        ),
    )
    op.execute("UPDATE idempotency_requests SET caller = principal")
    op.alter_column("idempotency_requests", "caller", nullable=False)
    op.create_unique_constraint(
        "idempotency_requests_caller_key",
        "idempotency_requests",
        ["caller", "idempotency_key"],
    )
    op.create_check_constraint(
        "idempotency_requests_method_check",
        "idempotency_requests",
        "method IN ('POST', 'PUT', 'PATCH', 'DELETE')",
    )
    op.execute(_MIRROR_FUNCTION)
    op.execute(
        """
        CREATE TRIGGER idempotency_requests_mirror_caller
        BEFORE INSERT OR UPDATE OF caller, principal ON idempotency_requests
        FOR EACH ROW EXECUTE FUNCTION idempotency_requests_mirror_caller()
        """
    )


def downgrade() -> None:
    """Return to the legacy principal-only schema."""
    op.execute("DROP TRIGGER idempotency_requests_mirror_caller ON idempotency_requests")
    op.execute("DROP FUNCTION idempotency_requests_mirror_caller()")
    op.drop_constraint("idempotency_requests_method_check", "idempotency_requests", type_="check")
    op.drop_constraint("idempotency_requests_caller_key", "idempotency_requests", type_="unique")
    op.drop_column("idempotency_requests", "caller")
