"""Fold the notification notice into the privacy notice

Notifications had a second consent notice with its own version, its own two columns, its own
error class and its own slash command -- a parallel mechanism for the same idea. Two notices mean
two things to keep current, two things to translate, and two chances to gate a write on the wrong
one. The privacy notice now describes notifications, so this receipt has nothing left to record.

The channel switches stay. Folding the *notice* must not turn anything on: `web_enabled` and
`dm_enabled` remain independent opt-ins that default to false, and accepting the notice permits
notifications rather than enabling them.

There is deliberately no data migration. Copying `consented_at` onto `accounts` would forge a
receipt for text nobody read, and the release that lands this also supersedes the notice version,
so every carried-over receipt would be stale on arrival anyway. Everyone accepts once, through the
prompt, to a notice that now says notifications are covered.

The behavioural consequence is that a profile with `dm_enabled = true` whose account has not
accepted the current notice stops receiving DMs, because the delivery queries now join `accounts`
instead of reading this table's receipt. That is the intended reading: the gate moved, it did not
disappear.

Revision ID: a3f9c2e5b7d1
Revises: f2d5b8c3a9e7
Create Date: 2026-08-18 13:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a3f9c2e5b7d1"
down_revision: str | Sequence[str] | None = "f2d5b8c3a9e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_TABLE_COMMENT = "A notification-specific notice receipt and independent channel preferences."

_TABLE_COMMENT = """Independent notification channel switches.

Carries no consent receipt. Notifications are covered by the one privacy notice, whose receipt
lives on `accounts`; a row here means "these switches", not "this person agreed"."""


def upgrade() -> None:
    op.drop_constraint("notification_profiles_notice_receipt_complete", "notification_profiles", type_="check")
    op.drop_column("notification_profiles", "notice_version")
    op.drop_column("notification_profiles", "consented_at")
    op.create_table_comment("notification_profiles", _TABLE_COMMENT, existing_comment=_OLD_TABLE_COMMENT)


def downgrade() -> None:
    op.create_table_comment("notification_profiles", _OLD_TABLE_COMMENT, existing_comment=_TABLE_COMMENT)
    op.add_column("notification_profiles", sa.Column("notice_version", sa.Text(), nullable=True))
    op.add_column(
        "notification_profiles",
        sa.Column("consented_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    # Left null on the way back: the receipts these columns held are gone, and inventing them
    # here would be the same forgery the upgrade refuses to commit.
    op.create_check_constraint(
        "notification_profiles_notice_receipt_complete",
        "notification_profiles",
        "(notice_version IS NULL) = (consented_at IS NULL)",
    )
