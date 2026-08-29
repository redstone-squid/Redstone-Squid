"""Cap verification code attempts

A verification code was six digits -- about 19.8 bits -- and the redemption looked it up by code
alone, never mentioning the Minecraft UUID it was issued for. One guess was therefore tested against
every outstanding code at once, and a hit attached the matched Java account to whoever typed it,
because the Discord identity comes from the caller rather than from the code. That is an identity
takeover primitive, and nothing capped the rate.

The code widens to ten digits and its digest becomes a keyed HMAC in the same change; this table is
the third lever. It counts *consecutive* failures, so a success clears the count and an honest user
who mistypes is never closer to a lockout than someone who never failed.

Keyed on `(provider, subject)` rather than on an account, with no foreign key: the guesser may have
no account yet, and creating one in order to rate-limit somebody would defeat the point.

No backfill. Codes live ten minutes, so the digest change invalidates at most one window and the
in-game `/link` reissues; there is no stored state to migrate.

Revision ID: a3b7c1d9e2f4
Revises: f68bbbd25847
Create Date: 2026-08-17 10:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a3b7c1d9e2f4"
down_revision: str | Sequence[str] | None = "f68bbbd25847"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PROVIDERS = ("discord", "java", "bedrock")


_TABLE_COMMENT = """Consecutive failed code redemptions for one external identity.

Keyed on `(provider, subject)` rather than on an account, because the guesser may not have an
account yet: a redemption is the first thing many callers ever do, and creating a row for
someone in order to rate-limit them would defeat the point. No foreign key for the same reason.

The counter is *consecutive*: a success clears it, so an honest user who mistypes twice and then
gets it right is never closer to a lockout than someone who never failed."""


def upgrade() -> None:
    """Apply this revision."""
    providers = ", ".join(f"'{provider}'" for provider in _PROVIDERS)
    op.create_table(
        "verification_attempts",
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("provider", "subject", name="verification_attempts_pkey"),
        sa.CheckConstraint(f"provider IN ({providers})", name="verification_attempts_provider_valid"),
        sa.CheckConstraint("consecutive_failures >= 0", name="verification_attempts_failures_non_negative"),
        # `Base` turns the model's class docstring into the table comment, so the migration has to
        # carry the same text or `alembic check` reports drift.
        comment=_TABLE_COMMENT,
    )


def downgrade() -> None:
    """Revert this revision."""
    op.drop_table("verification_attempts")
