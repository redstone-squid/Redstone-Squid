"""Retire the message tracking columns

`messages` now holds only message facts. Every reason the bot had for caring about
a message is expressed by something referencing it — `discord_posts` for the ones it
owns and renders, `build_source_messages` for the ones a build came from — so the
`purpose`, owner and projection columns have no remaining readers.

`server_id` becomes `guild_id`, matching every other table and discord.py itself;
`server_settings.server_id` was the odd one out that named it.

Also drops `get_unsent_builds`, which reads two of the removed columns and whose
only Python caller raised NotImplementedError, and the projection triggers, which
wrote a desired revision onto message rows. Desired state lives on the queue row and
applied state on the post row, so there is nothing left to project.

Revision ID: b1c2d3e4f5a7
Revises: a6b1c2d3e4f5
Create Date: 2026-08-15 19:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from squid.persistence.types import InstantUTC

revision: str = "b1c2d3e4f5a7"
down_revision: str | Sequence[str] | None = "a6b1c2d3e4f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    op.execute("DROP TRIGGER IF EXISTS update_messages_updated_at ON public.messages")
    op.execute("DROP TRIGGER IF EXISTS messages_initialize_discord_projection ON public.messages")
    op.execute("DROP TRIGGER IF EXISTS discord_sync_queue_project_desired_state ON public.discord_sync_queue")
    op.execute("DROP FUNCTION IF EXISTS public.initialize_discord_message_projection()")
    op.execute("DROP FUNCTION IF EXISTS public.project_discord_message_desired_state()")
    op.execute("DROP FUNCTION IF EXISTS public.get_unsent_builds(bigint)")

    op.drop_index("messages_projection_pending_idx", table_name="messages")
    for name in (
        "messages_projection_identity_complete",
        "messages_projection_resource_kind_check",
        "messages_desired_action_check",
        "messages_projection_revisions_valid",
    ):
        op.drop_constraint(name, "messages", type_="check")

    op.drop_constraint("public_messages_build_id_fkey", "messages", type_="foreignkey")
    op.drop_constraint("messages_vote_session_id_fkey", "messages", type_="foreignkey")
    for column in (
        "purpose",
        "build_id",
        "vote_session_id",
        "projection_resource_kind",
        "projection_source_key",
        "desired_action",
        "desired_revision",
        "applied_revision",
        "updated_at",
    ):
        op.drop_column("messages", column)

    op.alter_column("messages", "server_id", new_column_name="guild_id", existing_type=sa.BigInteger())
    op.create_table_comment(
        "messages",
        "A Discord message the bot has seen.\n\nOne row per Discord message, holding only what is true about the message itself.\nWhy it matters is expressed by the tables that reference it: `discord_posts` for\nthe messages the bot owns and renders, `build_source_messages` for the ones a\nbuild was inferred from.",
        existing_comment="A message associated with a build or vote session.",
        schema=None,
    )


def downgrade() -> None:
    """Revert this revision when the operation is safe.

    The dropped columns held tracking state that has since moved, so they come back
    empty rather than reconstructed; only the shape is restored.
    """
    op.create_table_comment(
        "messages",
        "A message associated with a build or vote session.",
        existing_comment="A Discord message the bot has seen.\n\nOne row per Discord message, holding only what is true about the message itself.\nWhy it matters is expressed by the tables that reference it: `discord_posts` for\nthe messages the bot owns and renders, `build_source_messages` for the ones a\nbuild was inferred from.",
        schema=None,
    )
    op.alter_column("messages", "guild_id", new_column_name="server_id", existing_type=sa.BigInteger())
    op.add_column("messages", sa.Column("purpose", sa.Text(), nullable=True))
    op.add_column("messages", sa.Column("build_id", sa.BigInteger(), nullable=True))
    op.add_column("messages", sa.Column("vote_session_id", sa.BigInteger(), nullable=True))
    op.add_column("messages", sa.Column("projection_resource_kind", sa.Text(), nullable=True))
    op.add_column("messages", sa.Column("projection_source_key", sa.Text(), nullable=True))
    op.add_column(
        "messages", sa.Column("desired_action", sa.Text(), server_default=sa.text("'refresh'"), nullable=False)
    )
    op.add_column(
        "messages", sa.Column("desired_revision", sa.BigInteger(), server_default=sa.text("1"), nullable=False)
    )
    op.add_column(
        "messages", sa.Column("applied_revision", sa.BigInteger(), server_default=sa.text("1"), nullable=False)
    )
    op.add_column("messages", sa.Column("updated_at", InstantUTC(timezone=True), nullable=True))
    op.create_foreign_key(
        "public_messages_build_id_fkey", "messages", "builds", ["build_id"], ["id"], ondelete="SET NULL"
    )
    op.create_foreign_key(
        "messages_vote_session_id_fkey", "messages", "vote_sessions", ["vote_session_id"], ["id"], ondelete="SET NULL"
    )
    op.create_check_constraint(
        "messages_projection_identity_complete",
        "messages",
        "(projection_resource_kind IS NULL) = (projection_source_key IS NULL)",
    )
    op.create_check_constraint(
        "messages_projection_resource_kind_check",
        "messages",
        "projection_resource_kind IS NULL OR projection_resource_kind IN ('build', 'vote_session')",
    )
    op.create_check_constraint("messages_desired_action_check", "messages", "desired_action IN ('refresh', 'delete')")
    op.create_check_constraint(
        "messages_projection_revisions_valid",
        "messages",
        "desired_revision > 0 AND applied_revision > 0 AND applied_revision <= desired_revision",
    )
    op.create_index(
        "messages_projection_pending_idx",
        "messages",
        ["desired_revision"],
        unique=False,
        postgresql_where=sa.text("projection_resource_kind IS NOT NULL AND desired_revision > applied_revision"),
    )
    op.execute(
        "CREATE TRIGGER update_messages_updated_at BEFORE UPDATE ON public.messages "
        "FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column()"
    )
