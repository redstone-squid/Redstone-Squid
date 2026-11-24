"""Record impersonation sessions and their uses."""

from alembic import op

revision = "9a3c0b18f4ed"
down_revision = "83e2b6a0d92c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE impersonation_sessions (
        id uuid PRIMARY KEY, operator_id integer NOT NULL, target_id integer NOT NULL,
        binding text NOT NULL, reason text NOT NULL, created_at timestamptz NOT NULL,
        expires_at timestamptz NOT NULL, revoked_at timestamptz
    )""")
    op.execute("""CREATE TABLE impersonation_uses (
        id uuid PRIMARY KEY, session_id uuid NOT NULL REFERENCES impersonation_sessions(id),
        operation text NOT NULL, created_at timestamptz NOT NULL, commits integer NOT NULL
    )""")
    op.execute(
        "COMMENT ON TABLE impersonation_sessions IS 'A debugging session bound to its operator''s authenticated entry point.'"
    )
    op.execute(
        "COMMENT ON TABLE impersonation_uses IS 'An authorized impersonated operation tied to its originating session.'"
    )


def downgrade() -> None:
    op.drop_table("impersonation_uses")
    op.drop_table("impersonation_sessions")
