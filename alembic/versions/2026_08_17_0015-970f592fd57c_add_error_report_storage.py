"""Add error report storage

An unexpected failure showed its user a reference and kept nothing. The reference was only
resolvable by grepping the host's log files, which is not something a moderator reading a
complaint on Discord can do, so in practice a reported error was unrecoverable unless somebody
with shell access happened to look within the rotation window.

This table holds one row per captured failure, indexed on both widths of the reference: the
short form a Discord card shows, and the full correlation ID that appears in logs and the
`Request-Id` response header. Neither is unique. The short form is a 48-bit prefix, so a
collision is possible even though it is vanishingly unlikely, and a unique constraint would
turn that into a refusal to record the second failure rather than an ambiguous lookup --
exactly backwards for a diagnostic store.

Rows expire: `expires_at` is stamped on write from the configured retention window and the
worker sweeps past it, the same shape as `idempotency_requests`.

Revision ID: 970f592fd57c
Revises: c9d0e1f2a3b8
Create Date: 2026-08-17 00:15:32.850156+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from squid.persistence.types import InstantUTC

revision: str = "970f592fd57c"
down_revision: str | Sequence[str] | None = "c9d0e1f2a3b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    # Comments mirror the model's attribute docstrings, which `Base` turns into column
    # comments; omitting them here is drift the autogenerate check catches.
    op.create_table(
        "error_reports",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "correlation_id",
            sa.Text(),
            nullable=False,
            comment="The full correlation ID, as it appears in logs and the Request-Id response header.",
        ),
        sa.Column(
            "reference",
            sa.Text(),
            nullable=False,
            comment=(
                "The shortened form shown to the user, indexed because it is what they quote back.\n\n"
                "Not unique: it is a 48-bit prefix of the correlation ID, so a collision is possible even\n"
                "though it is vanishingly unlikely, and a unique constraint would turn that into a failure to\n"
                "record the second error rather than an ambiguous lookup."
            ),
        ),
        sa.Column("occurred_at", InstantUTC(timezone=True), nullable=False, comment="When the failure was captured."),
        sa.Column("expires_at", InstantUTC(timezone=True), nullable=False, comment="When retention drops this report."),
        sa.Column(
            "surface",
            sa.Text(),
            nullable=False,
            comment="Which transport failed: an application command, a view callback, a route, a worker job.",
        ),
        sa.Column(
            "origin",
            sa.Text(),
            nullable=True,
            comment="The command name, route, or job the failure came from, when the surface knows it.",
        ),
        sa.Column("exception_type", sa.Text(), nullable=False, comment="Qualified name of the raised exception class."),
        sa.Column(
            "message",
            sa.Text(),
            nullable=False,
            comment="The exception's own string form, which is never shown to the user who triggered it.",
        ),
        sa.Column(
            "error_code", sa.Text(), nullable=True, comment="The application ErrorCode, when the failure carried one."
        ),
        sa.Column(
            "traceback",
            sa.Text(),
            nullable=False,
            comment="Rendered traceback, truncated from the front so the frames nearest the failure survive.",
        ),
        sa.Column(
            "context",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="Redacted diagnostic context. Never contains stable Discord account identifiers.",
        ),
        sa.Column(
            "log_tail",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="What the process logged under this correlation ID before failing, oldest first.",
        ),
        sa.PrimaryKeyConstraint("id"),
        comment="One unexpected failure, retained so the reference its user was shown can be resolved.",
    )
    op.create_index("error_reports_correlation_id_idx", "error_reports", ["correlation_id"], unique=False)
    op.create_index("error_reports_expires_at_idx", "error_reports", ["expires_at"], unique=False)
    op.create_index("error_reports_occurred_at_idx", "error_reports", ["occurred_at"], unique=False)
    op.create_index("error_reports_reference_idx", "error_reports", ["reference"], unique=False)


def downgrade() -> None:
    """Revert this revision when the operation is safe."""
    op.drop_index("error_reports_reference_idx", table_name="error_reports")
    op.drop_index("error_reports_occurred_at_idx", table_name="error_reports")
    op.drop_index("error_reports_expires_at_idx", table_name="error_reports")
    op.drop_index("error_reports_correlation_id_idx", table_name="error_reports")
    op.drop_table("error_reports")
