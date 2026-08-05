"""Fix REST API search prerequisites.

Revision ID: a4c8e2f6b913
Revises: 7f2c9d4e6a81
Create Date: 2026-08-08 11:00:00+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a4c8e2f6b913"
down_revision: str | Sequence[str] | None = "7f2c9d4e6a81"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_GET_OUTDATED_MESSAGES = """
CREATE FUNCTION public.get_outdated_messages(server_id_input bigint) RETURNS SETOF public.messages
    LANGUAGE plpgsql
    AS $$begin
    return query select messages.*
    from messages join builds
    on (messages.submission_id = builds.submission_id)
    where messages.last_updated < builds.last_update
    and messages.server_id = server_id_input
    and builds.submission_status = 1;
  end;$$;
"""


def upgrade() -> None:
    """Remove the broken dead function and refresh build search projections."""
    op.execute("DROP FUNCTION IF EXISTS public.get_outdated_messages(bigint)")
    op.execute(
        """
        INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
        SELECT 'build', id::text, 'upsert', now() FROM public.builds
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = EXCLUDED.action,
            enqueued_at = EXCLUDED.enqueued_at,
            attempts = 0,
            locked_at = NULL,
            last_error = NULL
        """
    )


def downgrade() -> None:
    """Restore the retired function for schema compatibility with the prior revision."""
    op.execute(_GET_OUTDATED_MESSAGES)
