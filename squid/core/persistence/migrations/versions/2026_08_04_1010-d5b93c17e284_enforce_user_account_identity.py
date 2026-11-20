"""enforce user account identity

Revision ID: d5b93c17e284
Revises: c1f7a2d84b30
Create Date: 2026-08-04 10:10:00+00:00
"""

from collections.abc import Sequence

from alembic import op

CONSENT_CUTOFF = "2026-08-04T00:00:00+00:00"

revision: str = "d5b93c17e284"
down_revision: str | Sequence[str] | None = "c1f7a2d84b30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Make `users` uniquely addressable and tie the consent receipt to the Minecraft link.

    Repository code has always assumed one row per Discord ID and per Minecraft
    UUID without the database enforcing it, so `get_one_or_none(discord_id=...)`
    could raise on duplicates.
    """
    # Fold duplicates onto the earliest row before the constraints can be added.
    for column in ("discord_id", "minecraft_uuid"):
        op.execute(
            f"""
            CREATE TEMP TABLE user_merge_{column} AS
            SELECT u.id AS duplicate_id, keep.id AS keep_id
            FROM public.users u
            JOIN (
                SELECT {column} AS value, min(id) AS id
                FROM public.users
                WHERE {column} IS NOT NULL
                GROUP BY {column}
                HAVING count(*) > 1
            ) keep ON keep.value = u.{column}
            WHERE u.id <> keep.id
            """
        )
        op.execute(
            f"""
            UPDATE public.creator_aliases ca
            SET user_id = m.keep_id
            FROM user_merge_{column} m
            WHERE ca.user_id = m.duplicate_id
            """
        )
        op.execute(
            f"""
            DELETE FROM public.creator_alias_claims c
            USING user_merge_{column} m
            WHERE c.user_id = m.duplicate_id
            """
        )
        op.execute(f"DELETE FROM public.users u USING user_merge_{column} m WHERE u.id = m.duplicate_id")
        op.execute(f"DROP TABLE user_merge_{column}")

    op.create_unique_constraint("users_discord_id_key", "users", ["discord_id"])
    op.create_unique_constraint("users_minecraft_uuid_key", "users", ["minecraft_uuid"])

    # The cutoff grandfathers rows linked before consent receipts existed
    # rather than fabricating a receipt for them, and rather than silently
    # unlinking accounts that predate the notice. They are re-prompted on their
    # next /account link. `NOT VALID` would say this more directly, but
    # SQLAlchemy reflects it as a dialect option Alembic cannot consume, so the
    # drift check would crash.
    op.create_check_constraint(
        "users_minecraft_link_requires_consent",
        "users",
        f"minecraft_uuid IS NULL OR consent_version IS NOT NULL OR created_at < TIMESTAMPTZ '{CONSENT_CUTOFF}'",
    )
    op.create_table_comment(
        "users",
        "An account we hold a relationship with, linking Discord and Minecraft identities.",
        existing_comment="A user in the system, which can be linked to both Discord and Minecraft accounts.",
    )


def downgrade() -> None:
    """Drop the account identity constraints."""
    op.create_table_comment(
        "users",
        "A user in the system, which can be linked to both Discord and Minecraft accounts.",
        existing_comment="An account we hold a relationship with, linking Discord and Minecraft identities.",
    )
    op.drop_constraint("users_minecraft_link_requires_consent", "users", type_="check")
    op.drop_constraint("users_minecraft_uuid_key", "users", type_="unique")
    op.drop_constraint("users_discord_id_key", "users", type_="unique")
