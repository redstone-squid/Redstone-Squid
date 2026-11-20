"""Drop the identity subject format CHECK

`account_identities_subject_format_check` restated, in SQL, what a canonical subject
looks like for each of the three providers. It is now stated once in
`AccountIdentity.for_provider`, whose `match` is exhaustive over `IdentityProvider`, so
adding a provider is a type error until its subject format is written down. Two
authorities means a fourth provider needs a migration nobody will remember to write, and
the SQL half cannot express the useful part anyway -- it rejects a non-canonical Java
UUID that the domain instead *normalizes* into canonical form.

`account_identities_provider_check` stays: membership is a write-time safety net worth
keeping, and it is generated from the enum in the model, so it cannot drift.

Revision ID: d4e5f6a7b8c3
Revises: c3d4e5f6a7b2
Create Date: 2026-08-16 13:00:00.000000+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d4e5f6a7b8c3"
down_revision: str | Sequence[str] | None = "c3d4e5f6a7b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SUBJECT_FORMAT_CHECK = (
    "(provider <> 'discord' OR subject ~ '^[1-9][0-9]*$') AND "
    "(provider <> 'bedrock' OR subject ~ '^[1-9][0-9]*$') AND "
    "(provider <> 'java' OR subject ~ "
    "'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')"
)


def upgrade() -> None:
    op.drop_constraint("account_identities_subject_format_check", "account_identities", type_="check")


def downgrade() -> None:
    op.create_check_constraint("account_identities_subject_format_check", "account_identities", _SUBJECT_FORMAT_CHECK)
