"""Record Discord messages as deduplicated facts

Splits "what is true about this Discord message" from "why we care about it".
`messages` keeps one row per snowflake carrying only message-level truth, and
`build_source_messages` links builds to the messages they came from.

That link is many-to-many in both directions, which the replaced
`builds.original_message_id` could not express: a submission routinely spans a
body message plus follow-up images, and a build-log message often yields a whole
bundle of builds. Dropping the column also removes a circular foreign key, since
`messages.build_id` still points the other way until its writers move onto
`discord_posts`.

Revision ID: b1c2d3e4f5a6
Revises: 828efb4cf6e0
Create Date: 2026-08-15 12:30:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

import squid.persistence.types
from alembic import op

revision: str = "b1c2d3e4f5a6"
down_revision: str | Sequence[str] | None = "828efb4cf6e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    op.create_table(
        "build_source_messages",
        sa.Column("build_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["build_id"], ["builds.id"], name="build_source_messages_build_id_fkey", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["message_id"], ["messages.id"], name="build_source_messages_message_id_fkey", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("build_id", "message_id"),
        comment=(
            "Association table between builds and the Discord messages they came from.\n\n"
            "Many-to-many in both directions: one submission can span a body message plus\n"
            "follow-up images, and one build-log message can yield several builds at once.\n"
            "The message side is RESTRICT because a message row is a retained fact that\n"
            "outlives the builds referring to it; deleting a build only drops the link."
        ),
    )

    op.add_column("messages", sa.Column("created_at", squid.persistence.types.InstantUTC(timezone=True), nullable=True))
    op.add_column(
        "messages",
        sa.Column(
            "observed_at",
            squid.persistence.types.InstantUTC(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.add_column("messages", sa.Column("edited_at", squid.persistence.types.InstantUTC(timezone=True), nullable=True))
    op.add_column("messages", sa.Column("deleted_at", squid.persistence.types.InstantUTC(timezone=True), nullable=True))

    # DMs are legitimate message facts, and a fact row no longer has to justify itself
    # with a tracking role, so both of these stop being required.
    op.alter_column("messages", "server_id", existing_type=sa.BIGINT(), nullable=True)
    op.alter_column(
        "messages",
        "purpose",
        existing_type=sa.TEXT(),
        nullable=True,
        comment=(
            "Legacy tracking role; NULL for plain observed facts. Removed once every writer moves to discord_posts."
        ),
        existing_comment="The reason why the message is stored in the database",
    )

    # Carry the existing provenance across before the column that held it is dropped.
    op.execute(
        """
        INSERT INTO build_source_messages (build_id, message_id, position)
        SELECT b.id, b.original_message_id, 0
        FROM builds b
        WHERE b.original_message_id IS NOT NULL
        ON CONFLICT DO NOTHING
        """
    )
    op.drop_constraint(op.f("builds_original_message_id_fkey"), "builds", type_="foreignkey")
    op.drop_column("builds", "original_message_id")


def downgrade() -> None:
    """Revert this revision when the operation is safe."""
    op.add_column("builds", sa.Column("original_message_id", sa.BIGINT(), autoincrement=False, nullable=True))
    op.create_foreign_key(
        op.f("builds_original_message_id_fkey"),
        "builds",
        "messages",
        ["original_message_id"],
        ["id"],
        onupdate="CASCADE",
        ondelete="SET NULL",
    )
    # Only the first source message fits the single column, so refuse to discard the
    # rest rather than silently losing provenance the new schema could express.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM build_source_messages GROUP BY build_id HAVING count(*) > 1) THEN
                RAISE EXCEPTION 'cannot downgrade while a build retains more than one source message';
            END IF;
        END $$
        """
    )
    op.execute(
        """
        UPDATE builds b
        SET original_message_id = s.message_id
        FROM build_source_messages s
        WHERE s.build_id = b.id
        """
    )
    op.drop_table("build_source_messages")

    op.execute("UPDATE messages SET purpose = 'build_original_message' WHERE purpose IS NULL")
    op.alter_column(
        "messages",
        "purpose",
        existing_type=sa.TEXT(),
        nullable=False,
        comment="The reason why the message is stored in the database",
        existing_comment=(
            "Legacy tracking role; NULL for plain observed facts. Removed once every writer moves to discord_posts."
        ),
    )
    op.execute("DELETE FROM messages WHERE server_id IS NULL")
    op.alter_column("messages", "server_id", existing_type=sa.BIGINT(), nullable=False)
    op.drop_column("messages", "deleted_at")
    op.drop_column("messages", "edited_at")
    op.drop_column("messages", "observed_at")
    op.drop_column("messages", "created_at")
