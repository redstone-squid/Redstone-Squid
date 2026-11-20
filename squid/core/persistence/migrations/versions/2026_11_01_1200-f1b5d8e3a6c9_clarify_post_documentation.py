"""Clarify Discord post documentation.

Revision ID: f1b5d8e3a6c9
Revises: e0a4c7d2f5b8
"""

from alembic import op

revision: str = "f1b5d8e3a6c9"
down_revision: str | None = "e0a4c7d2f5b8"
branch_labels: str | None = None
depends_on: str | None = None

_OLD_TABLE_COMMENT = """A Discord message the bot owns and keeps rendered for some resource.

Holds only applied state. What the post *should* look like lives in the matching
`discord_sync_queue` row, so staleness is `applied_revision < queue.generation`
rather than a desired revision copied onto every post by a trigger."""
_TABLE_COMMENT = "A bot-owned Discord message displaying a build, vote, or other resource."
_COLUMN_COMMENTS: dict[str, tuple[str | None, str]] = {
    "channel_id": (
        "Denormalised from `messages.channel_id`: a unique index cannot span a join.",
        "Copied from the message so the unique index can limit live posts per channel.",
    ),
    "resource_kind": (None, "The type of resource displayed by this message, used to select its renderer."),
    "resource_key": (None, "The resource's identifier, such as a build ID or a starboard/message ID pair."),
    "surface": (None, "The presentation to use, such as a build card or a vote card."),
    "applied_revision": (
        "The queue generation this post was last rendered at.",
        "The last rendered queue generation; a larger queued generation means an update is pending.",
    ),
    "rendered_at": (None, "When a rendered update was last recorded; None until the first update."),
    "suppressed_at": (
        "Set when the post was deleted outside the bot. Renderers choose whether to repost.",
        "When an external deletion was recorded. Each renderer decides whether to publish a replacement.",
    ),
}


def upgrade() -> None:
    op.create_table_comment("discord_posts", _TABLE_COMMENT, existing_comment=_OLD_TABLE_COMMENT)
    for column, (old, new) in _COLUMN_COMMENTS.items():
        op.alter_column("discord_posts", column, comment=new, existing_comment=old)


def downgrade() -> None:
    for column, (old, new) in _COLUMN_COMMENTS.items():
        op.alter_column("discord_posts", column, comment=old, existing_comment=new)
    op.create_table_comment("discord_posts", _OLD_TABLE_COMMENT, existing_comment=_TABLE_COMMENT)
