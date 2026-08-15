"""Move vote cards onto discord_posts

Vote sessions were addressed by joining `messages` on `vote_session_id` with
`purpose = 'vote'`. That column is how a session found its own cards, which meant
the tracking table doubled as the session's presentation index.

Cards become ordinary `discord_posts` rows keyed by resource, so `messages` is left
holding only message facts. A build review renders the build it is voting on and a
delete-log or poll card stands alone, so they are distinguished by surface.

Revision ID: f5a6b1c2d3e4
Revises: e4f5a6b1c2d3
Create Date: 2026-08-15 17:00:00.000000+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f5a6b1c2d3e4"
down_revision: str | Sequence[str] | None = "e4f5a6b1c2d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    op.drop_constraint("discord_posts_surface_check", "discord_posts", type_="check")
    op.create_check_constraint(
        "discord_posts_surface_check",
        "discord_posts",
        "surface IN ('build_card', 'build_review', 'vote_card', 'starboard_entry')",
    )

    op.execute(
        """
        INSERT INTO discord_posts (
            message_id, channel_id, resource_kind, resource_key, surface, applied_revision, posted_at, rendered_at
        )
        SELECT
            m.id,
            m.channel_id,
            'vote_session',
            m.vote_session_id::text,
            CASE WHEN vs.kind = 'build' THEN 'build_review' ELSE 'vote_card' END,
            m.applied_revision,
            COALESCE(m.observed_at, now()),
            m.updated_at
        FROM messages m
        JOIN vote_sessions vs ON vs.id = m.vote_session_id
        WHERE m.purpose = 'vote' AND m.channel_id IS NOT NULL
        ON CONFLICT DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE messages
        SET purpose = NULL, projection_resource_kind = NULL, projection_source_key = NULL
        WHERE purpose = 'vote'
        """
    )


def downgrade() -> None:
    """Revert this revision when the operation is safe."""
    op.execute(
        """
        UPDATE messages m
        SET purpose = 'vote',
            vote_session_id = p.resource_key::bigint,
            projection_resource_kind = 'vote_session',
            projection_source_key = p.resource_key,
            applied_revision = p.applied_revision,
            desired_revision = GREATEST(m.desired_revision, p.applied_revision)
        FROM discord_posts p
        WHERE p.message_id = m.id AND p.resource_kind = 'vote_session'
        """
    )
    op.execute("DELETE FROM discord_posts WHERE resource_kind = 'vote_session'")
    op.execute("UPDATE discord_posts SET surface = 'build_card' WHERE surface = 'vote_card'")
    op.drop_constraint("discord_posts_surface_check", "discord_posts", type_="check")
    op.create_check_constraint(
        "discord_posts_surface_check",
        "discord_posts",
        "surface IN ('build_card', 'build_review', 'starboard_entry')",
    )
