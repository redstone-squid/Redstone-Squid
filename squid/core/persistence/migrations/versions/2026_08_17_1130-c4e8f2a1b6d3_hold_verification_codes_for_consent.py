"""Hold verification codes for consent

The account-link consent prompt runs before the code is redeemed, and the only code path was the
atomic redemption itself, which spends the code. So the prompt could not name the Minecraft account
it was about to link, the creator credit that would move, or anything else concrete -- it could only
describe categories in prose. That is why the notice was a wall of text.

These two columns let a code be *held*: reserved, previewed, then either committed or released. A
hold is the durable substitute for a transaction, which cannot be kept open across a 120-second wait
on a human.

The hold is keyed on a token digest and says nothing about who took it. That is deliberate and
load-bearing: the account is created only after consent is given, and the notice promises that
cancelling stores no account information, so a hold keyed on an account would have made the notice
false.

Nothing reaps a lapsed hold. `reserved_until` in the past simply stops counting, so a crashed process
costs one prompt's delay rather than a stuck code, and the player can always mint a fresh code in
game.

No backfill: both columns are nullable, and an unreserved code is exactly a row with both NULL.

Revision ID: c4e8f2a1b6d3
Revises: a3b7c1d9e2f4
Create Date: 2026-08-17 11:30:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c4e8f2a1b6d3"
down_revision: str | Sequence[str] | None = "a3b7c1d9e2f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RESERVED_TOKEN_COMMENT = """Digest of the token held by whoever is currently being shown this code's consent prompt.

A digest rather than the token, for the same reason `code` is one. Deliberately says nothing
about *who* reserved it: the prompt runs before an account exists, and the notice promises that
cancelling stores no account information, so a reservation identifies nobody."""

_RESERVED_UNTIL_COMMENT = """When the hold lapses, freeing the code without anything having to reap it.

A crashed process therefore costs one prompt's worth of delay rather than a stuck code, and the
legitimate owner can always mint a fresh one from the game."""


def upgrade() -> None:
    """Apply this revision."""
    op.add_column(
        "verification_codes",
        sa.Column("reserved_token", sa.Text(), nullable=True, comment=_RESERVED_TOKEN_COMMENT),
    )
    op.add_column(
        "verification_codes",
        sa.Column("reserved_until", sa.DateTime(timezone=True), nullable=True, comment=_RESERVED_UNTIL_COMMENT),
    )
    op.create_check_constraint(
        "verification_codes_reservation_complete",
        "verification_codes",
        "(reserved_token IS NULL) = (reserved_until IS NULL)",
    )


def downgrade() -> None:
    """Revert this revision."""
    op.drop_constraint("verification_codes_reservation_complete", "verification_codes", type_="check")
    op.drop_column("verification_codes", "reserved_until")
    op.drop_column("verification_codes", "reserved_token")
