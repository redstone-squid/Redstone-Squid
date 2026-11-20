"""Take the permission graph lock before touching individual rules."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a9b2c5d8e1f4"
down_revision: str | Sequence[str] | None = "f8a1b4c7d0e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (
    "permission_roles",
    "permission_role_patterns",
    "permission_role_includes",
    "permission_grants",
    "permission_role_assignments",
)


def _replace(timing: str) -> None:
    for table in _TABLES:
        op.execute(f"DROP TRIGGER {table}_bump_epoch ON public.{table}")
        op.execute(
            f"CREATE TRIGGER {table}_bump_epoch {timing} INSERT OR DELETE OR UPDATE ON public.{table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION public.bump_permission_epoch()"
        )


def upgrade() -> None:
    op.add_column("permission_roles", sa.Column("withdrawn", sa.Boolean(), nullable=False, server_default=sa.false()))
    _replace("BEFORE")


def downgrade() -> None:
    _replace("AFTER")
    op.drop_column("permission_roles", "withdrawn")
