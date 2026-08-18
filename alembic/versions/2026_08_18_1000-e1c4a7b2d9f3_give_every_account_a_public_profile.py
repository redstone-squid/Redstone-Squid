"""Give every account a public profile

An account has been an identity anchor and nothing more: a public creator id, a created timestamp,
and a consent receipt. The public surface built on it -- `GET /v1/creators/{id}` -- can therefore
serve a UUID and a list of alias names, and nothing a person would recognise as themselves. There
is no display name, no bio, no avatar, and no way to link out.

This adds the storage for a profile that is public by default, plus the two controls that make
"public by default" acceptable: a per-identity `is_public` flag, and a whole-profile `hidden` flag.
The per-identity flag exists because the decisions genuinely differ -- publishing an IGN and
publishing a Discord account are not the same choice -- and `hidden` exists for people who want a
creator page that is only their credits.

`account_profiles` is a child table rather than columns on `accounts`. The account row is what
thirty-odd foreign keys point at and what the link and merge paths lock `FOR UPDATE`; profile text
is user-edited prose with an entirely different write cadence. Folding it into the anchor would
make every identity read carry a bio it does not want.

The CHECKs are length and shape only. Normalization -- NFKC folding, trimming, rejecting control
characters, validating link URLs -- lives in the domain, the same split `creator_aliases`
documented when its normalized name moved out of SQL: the application is the definition, and the
constraint is a guard against a hand-written INSERT.

`links` is JSONB rather than a child table. Nothing queries a link, and every write replaces the
whole list from a single owner, so a table would buy referential integrity to nothing while adding
a join to the hottest public read.

`avatar_identity_id` is `ON DELETE SET NULL` on purpose: unlinking the identity an avatar came from
must clear the avatar rather than leave a render pointing at a subject we no longer hold. That the
identity belongs to the *same* account is checked in the repository, because the composite foreign
key that would enforce it here cannot coexist with `SET NULL` -- it would have to null part of the
referenced key.

Backfill inserts a default profile row for every existing account, so the common read is a plain
join. Reads still coalesce, so an account that somehow lacks a row renders as an empty profile
rather than a 500.

`is_public` defaults true and is added with a server default, which Postgres stores in the catalog
without rewriting the table. Existing linked identities therefore become visible on their owner's
new profile page. That is the intended default, and it is safe to take here because the deployment
has no users beyond the maintainer dogfooding it; the same change made after an alpha would need a
notice bump instead.

Revision ID: e1c4a7b2d9f3
Revises: b9d3e6a1f8c5
Create Date: 2026-08-18 10:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e1c4a7b2d9f3"
down_revision: str | Sequence[str] | None = "b9d3e6a1f8c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE_COMMENT = """What an account chooses to publish about itself on its creator page.

A child of `accounts` rather than more columns on it. The account row is an identity anchor
that thirty-odd foreign keys point at and that link and merge paths lock `FOR UPDATE`;
profile text is user-edited prose with an entirely different write cadence, and widening the
anchor to carry it would make every identity read pay for a bio."""

_DISPLAY_NAME_COMMENT = """Presentation only, deliberately not a `creator_aliases` name: renaming yourself here moves
no build credit and needs no staff review."""

_LINKS_COMMENT = """External links as `[{"label": ..., "url": ...}]`.

JSONB rather than a child table: nothing queries links, and every write replaces the whole
list from one owner, so a table would buy referential integrity to nothing and cost a join on
the hottest public read."""

_HIDDEN_COMMENT = """Whether to withhold the profile. A hidden profile still serves its aliases and build
credits, because a creator page that vanished would strand every build crediting it."""

_AVATAR_COMMENT = """The linked identity this profile's avatar is rendered from.

`SET NULL` so unlinking that identity clears the avatar rather than leaving a render pointing
at a subject we no longer hold. Ownership — that the identity belongs to this same account —
is checked in the repository, since the composite foreign key that would enforce it here
cannot coexist with `ON DELETE SET NULL`."""

_IS_PUBLIC_COMMENT = """Whether this identity appears on the account's public creator profile."""

_AVATAR_KEY_COMMENT = """Provider rendering key, where the subject alone is not enough to build an avatar URL.

Discord avatar URLs need the hash, which only the gateway supplies. Java heads derive from
the UUID, so this stays NULL there."""


def upgrade() -> None:
    """Apply this revision."""
    op.create_table(
        "account_profiles",
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True, comment=_DISPLAY_NAME_COMMENT),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column("pronouns", sa.Text(), nullable=True),
        sa.Column(
            "links",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
            comment=_LINKS_COMMENT,
        ),
        sa.Column("hidden", sa.Boolean(), server_default=sa.text("false"), nullable=False, comment=_HIDDEN_COMMENT),
        sa.Column("avatar_identity_id", sa.BigInteger(), nullable=True, comment=_AVATAR_COMMENT),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "display_name IS NULL OR char_length(display_name) BETWEEN 1 AND 64",
            name="account_profiles_display_name_length",
        ),
        sa.CheckConstraint(
            "display_name IS NULL OR display_name = btrim(display_name)",
            name="account_profiles_display_name_trimmed",
        ),
        sa.CheckConstraint("bio IS NULL OR char_length(bio) BETWEEN 1 AND 500", name="account_profiles_bio_length"),
        sa.CheckConstraint(
            "pronouns IS NULL OR char_length(pronouns) BETWEEN 1 AND 40",
            name="account_profiles_pronouns_length",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(links) = 'array' AND jsonb_array_length(links) <= 10",
            name="account_profiles_links_shape",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name="account_profiles_account_id_fkey", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["avatar_identity_id"],
            ["account_identities.id"],
            name="account_profiles_avatar_identity_id_fkey",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("account_id", name="account_profiles_pkey"),
        comment=_TABLE_COMMENT,
    )
    op.create_index("account_profiles_avatar_identity_idx", "account_profiles", ["avatar_identity_id"])

    op.add_column(
        "account_identities",
        sa.Column(
            "is_public",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
            comment=_IS_PUBLIC_COMMENT,
        ),
    )
    op.add_column(
        "account_identities",
        sa.Column("avatar_key", sa.Text(), nullable=True, comment=_AVATAR_KEY_COMMENT),
    )

    # Every existing account gets the profile it would have been created with, so the common read
    # is a plain join rather than a left join plus a coalesce for rows that predate this table.
    op.execute("INSERT INTO account_profiles (account_id) SELECT id FROM accounts ON CONFLICT (account_id) DO NOTHING")


def downgrade() -> None:
    """Revert this revision."""
    op.drop_column("account_identities", "avatar_key")
    op.drop_column("account_identities", "is_public")
    op.drop_index("account_profiles_avatar_identity_idx", table_name="account_profiles")
    op.drop_table("account_profiles")
