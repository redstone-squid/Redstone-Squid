"""Use the token-fenced lease as the sole build lock."""

from alembic import op

revision: str = "d5f9a2b7e0c3"
down_revision: str | None = "c4e8f1a6d9b2"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("DROP TRIGGER set_locked_at ON public.builds")
    op.execute("DROP FUNCTION public.set_locked_at()")
    op.drop_constraint("builds_lock_lease_complete", "builds", type_="check")
    # Offline deployment invalidates any outstanding application lease.
    op.execute("UPDATE builds SET locked_at = NULL, lock_token = NULL, lock_expires_at = NULL")
    op.drop_column("builds", "is_locked")
    op.create_check_constraint(
        "builds_lock_lease_complete",
        "builds",
        "(locked_at IS NULL AND lock_token IS NULL AND lock_expires_at IS NULL) OR "
        "(locked_at IS NOT NULL AND lock_token IS NOT NULL AND lock_expires_at IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("builds_lock_lease_complete", "builds", type_="check")
    op.execute("ALTER TABLE builds ADD COLUMN is_locked boolean NOT NULL DEFAULT false")
    op.execute("UPDATE builds SET is_locked = lock_token IS NOT NULL")
    op.create_check_constraint(
        "builds_lock_lease_complete",
        "builds",
        "(is_locked AND lock_token IS NOT NULL AND lock_expires_at IS NOT NULL) "
        "OR (NOT is_locked AND lock_token IS NULL AND lock_expires_at IS NULL)",
    )
    op.execute(
        "CREATE FUNCTION public.set_locked_at() RETURNS trigger\n    LANGUAGE plpgsql\n    AS $$\nbegin\n  if new.is_locked then\n    new.locked_at := now();\n  else\n    new.locked_at := null;\n  end if;\n  return new;\nend;\n$$;"
    )
    op.execute(
        "CREATE TRIGGER set_locked_at BEFORE UPDATE ON public.builds FOR EACH ROW EXECUTE FUNCTION public.set_locked_at();"
    )
