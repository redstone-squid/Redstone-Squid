"""repair record recompute queue

Revision ID: 6f3d9c8a2b71
Revises: b4e7c1a93d52
Create Date: 2026-07-30 19:00:00+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "6f3d9c8a2b71"
down_revision: str | Sequence[str] | None = "b4e7c1a93d52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Replace invalid per-build hints with canonical full-kind rebuilds."""
    op.execute("TRUNCATE TABLE record_recompute_queue RESTART IDENTITY")
    op.execute(
        """
        INSERT INTO record_recompute_queue (scope_key, build_kind, reasons)
        VALUES
            ('door', 'door', '["taxonomy_cutover"]'::jsonb),
            ('extender', 'extender', '["taxonomy_cutover"]'::jsonb)
        """
    )


def downgrade() -> None:
    """Restore the taxonomy cutover's per-build queue shape."""
    op.execute("TRUNCATE TABLE record_recompute_queue RESTART IDENTITY")
    op.execute(
        """
        INSERT INTO record_recompute_queue (scope_key, build_kind, build_id, reasons)
        SELECT 'build:' || id::text, category, id, '["taxonomy_cutover"]'::jsonb
        FROM builds
        WHERE category IS NOT NULL
        """
    )
