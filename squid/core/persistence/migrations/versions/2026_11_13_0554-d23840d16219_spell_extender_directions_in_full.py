"""Store extender directions as Upward and Downward.

Submissions stored vertical extenders as "Upward" or "Downward", which build titles expect, but
the build editor stored "Up" or "Down". Records group extenders by the stored direction, so the
same direction formed two record categories. Rewrite the short forms.

Edit capture is disabled for the rewrite, as the direction itself does not change. Discord and
search updates are still queued, because titles change.

Revision ID: d23840d16219
Revises: 4fd493faba21
"""

from alembic import op

revision = "d23840d16219"
down_revision = "4fd493faba21"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE public.extenders DISABLE TRIGGER extenders_capture_edit")
    op.execute(
        """
        UPDATE public.extenders
        SET orientation = CASE lower(orientation) WHEN 'up' THEN 'Upward' ELSE 'Downward' END
        WHERE lower(orientation) IN ('up', 'down')
        """
    )
    op.execute("ALTER TABLE public.extenders ENABLE TRIGGER extenders_capture_edit")


def downgrade() -> None:
    # Both spellings were stored before this revision, so the full spelling is kept.
    pass
