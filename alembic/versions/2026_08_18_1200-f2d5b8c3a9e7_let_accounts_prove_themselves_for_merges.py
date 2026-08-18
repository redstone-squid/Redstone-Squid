"""Let accounts prove themselves for merges

`AccountService.merge_accounts` has been correct and unreachable since it was written. It demands
two `RecentAccountProof`s inside a ten-minute window, and nothing could produce the second one: a
session authenticates one account, and the whole point of a merge is that a person holds two.

This table is the missing half. The account to be absorbed mints a single-use code; the surviving
account redeems it. Minting is the absorbed side authenticating, redeeming is the surviving side
doing so, and the ticket carries the first proof across the gap between the two sessions. The
channel is a person who can sign into both, which is exactly the claim a merge asserts.

Keyed on `account_id`, not on the digest, so minting replaces rather than accumulates. One live
ticket per account is what keeps an eight-character code sufficient: a guesser gets one target at
a time, inside ten minutes, against route rate limits.

`expires_at` is deliberately the same ten minutes as `MERGE_PROOF_MAX_AGE_SECONDS`. Two windows
that could disagree would be a bug waiting for someone to tune one of them, so a ticket is
redeemable for exactly as long as the proof it stands for is accepted.

Only a digest is stored, keyed by the existing verification-code pepper. The plaintext is returned
once, at mint time, and is not recoverable afterwards -- the same treatment a verification code
gets, for the same reason: a leaked table should not hand anyone else's account away.

Nothing reaps expired tickets. An expired row simply stops matching, and the next mint for that
account overwrites it, so a stale ticket costs one row rather than a cleanup job.

Revision ID: f2d5b8c3a9e7
Revises: e1c4a7b2d9f3
Create Date: 2026-08-18 12:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f2d5b8c3a9e7"
down_revision: str | Sequence[str] | None = "e1c4a7b2d9f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE_COMMENT = """A live, single-use claim that one account consents to being absorbed by another.

A merge needs recent proof of *both* accounts, and no one session can hold both. The ticket is
that second proof, carried through the only channel the two sides share: a person who can sign
into each. Minting one is the absorbed side authenticating; redeeming it is the surviving side
doing so, inside the ticket's lifetime.

Keyed on the account rather than on the digest, so minting replaces. One account can only ever
have one live ticket, which is most of why an eight-character code is enough."""

_CODE_DIGEST_COMMENT = """Digest, never the code. The plaintext is shown once at mint time and never stored, exactly
as a verification code is."""

_EXPIRES_AT_COMMENT = """Doubles as the proof timestamp: a ticket is redeemable for exactly as long as
`RecentAccountProof` accepts the authentication that minted it."""


def upgrade() -> None:
    """Apply this revision."""
    op.create_table(
        "account_merge_tickets",
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("code_digest", sa.Text(), nullable=False, comment=_CODE_DIGEST_COMMENT),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False, comment=_EXPIRES_AT_COMMENT),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("expires_at > created_at", name="account_merge_tickets_expiry_after_creation"),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name="account_merge_tickets_account_id_fkey", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("account_id", name="account_merge_tickets_pkey"),
        comment=_TABLE_COMMENT,
    )
    # Redemption looks a ticket up by digest; the primary key answers the mint side only.
    op.create_index("account_merge_tickets_code_digest_idx", "account_merge_tickets", ["code_digest"])


def downgrade() -> None:
    """Revert this revision."""
    op.drop_index("account_merge_tickets_code_digest_idx", table_name="account_merge_tickets")
    op.drop_table("account_merge_tickets")
