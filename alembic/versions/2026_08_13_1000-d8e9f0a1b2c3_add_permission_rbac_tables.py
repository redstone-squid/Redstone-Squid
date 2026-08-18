"""Add permission RBAC tables.

Creates the storage for hierarchical permission nodes: pattern-carrying grants,
composable roles, an append-only audit log, and the epoch counter that lets the
bot, API and worker processes invalidate their rule-set caches.

Nothing reads these tables yet; see docs/plans/rbac.md.

Built-in roles are inserted with no pattern rows. Their pattern lists live in
`squid.permissions.domain.catalogue.BUILTIN_ROLES` on purpose: a list seeded here
would freeze the catalogue as it looked the day this ran, so every node added
afterwards would silently fall outside `global-admin`.

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
Create Date: 2026-08-13 10:00:00+00:00
"""

from collections.abc import Sequence
from typing import TypeVar

import sqlalchemy as sa
from alembic_utils.pg_function import PGFunction
from alembic_utils.pg_trigger import PGTrigger
from alembic_utils.replaceable_entity import ReplaceableEntity
from sqlalchemy.dialects import postgresql

from alembic import op
from squid.persistence.alembic_entities import alembic_util_entities

revision: str = "d8e9f0a1b2c3"
down_revision: str | Sequence[str] | None = "c7d8e9f0a1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FUNCTION_NAMES = {"bump_permission_epoch"}
_TRIGGER_NAMES = {
    "permission_grants_bump_epoch",
    "permission_role_assignments_bump_epoch",
    "permission_role_includes_bump_epoch",
    "permission_role_patterns_bump_epoch",
    "permission_roles_bump_epoch",
}

# Written out rather than taken from the shipped entity definitions, because
# `f0a1b2c3d4e5` drops the legacy table and removes this trigger from
# `postgres_entities.sql`. A revision must not depend on SQL a later revision
# deletes, or migrating a fresh database from scratch stops working.
_LEGACY_TIER_TRIGGER = (
    "CREATE TRIGGER global_administrators_bump_epoch "
    "AFTER INSERT OR DELETE OR UPDATE ON public.global_administrators "
    "FOR EACH STATEMENT EXECUTE FUNCTION public.bump_permission_epoch()"
)

_ONE_SUBJECT = "num_nonnulls(subject_account_id, subject_role_id) = 1"
_ROLE_SUBJECT_HAS_GUILD = "subject_role_id IS NULL OR subject_guild_id IS NOT NULL"
_ROLE_SUBJECT_STAYS_HOME = "subject_role_id IS NULL OR scope_guild_id IS NULL OR scope_guild_id = subject_guild_id"

# Ranks govern who may edit which role. They deliberately play no part in
# permission resolution, so reordering roles cannot change an authorization
# outcome.
_BUILTIN_ROLES = (
    ("owner", "Owner", "The bot owner. Holds everything, unconditionally.", 1000),
    (
        "global-admin",
        "Global administrator",
        "Application-wide moderation, short of destructive and permission-granting powers.",
        800,
    ),
    (
        "guild-admin",
        "Server administrator",
        "What Discord's Manage Server permission implies, as permission nodes.",
        500,
    ),
    ("trusted", "Trusted", "The legacy Trusted tier: schematic diagnostics and weighted votes.", 200),
)


def upgrade() -> None:
    """Create the RBAC tables, seed the epoch and the built-in roles, install the trigger."""
    op.create_table(
        "permission_roles",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("guild_id", sa.BigInteger(), nullable=True),
        sa.Column("builtin_key", sa.Text(), nullable=True),
        sa.Column("rank", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("protected", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_by_account_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("builtin_key IS NULL OR guild_id IS NULL", name="permission_roles_builtin_is_global"),
        sa.CheckConstraint("slug ~ '^[a-z][a-z0-9-]{1,31}$'", name="permission_roles_slug_format"),
        sa.ForeignKeyConstraint(
            ["created_by_account_id"],
            ["accounts.id"],
            name="permission_roles_created_by_fkey",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("builtin_key", name="permission_roles_builtin_key_key"),
        comment="A named bundle of permission patterns.",
    )
    op.execute(
        """
        ALTER TABLE public.permission_roles
        ADD CONSTRAINT permission_roles_guild_slug_key UNIQUE NULLS NOT DISTINCT (guild_id, slug)
        """
    )

    op.create_table(
        "permission_role_patterns",
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("pattern", sa.Text(), nullable=False),
        sa.Column("mode", sa.SmallInteger(), nullable=False),
        sa.Column("added_by_account_id", sa.Integer(), nullable=True),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("mode IN (1, -1)", name="permission_role_patterns_mode_check"),
        sa.ForeignKeyConstraint(
            ["added_by_account_id"],
            ["accounts.id"],
            name="permission_role_patterns_added_by_fkey",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["permission_roles.id"],
            name="permission_role_patterns_role_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("role_id", "pattern"),
        comment="One pattern a role includes or subtracts.",
    )

    op.create_table(
        "permission_role_includes",
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("included_role_id", sa.Integer(), nullable=False),
        sa.Column("added_by_account_id", sa.Integer(), nullable=True),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("role_id <> included_role_id", name="permission_role_includes_no_self_include"),
        sa.ForeignKeyConstraint(
            ["added_by_account_id"],
            ["accounts.id"],
            name="permission_role_includes_added_by_fkey",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["included_role_id"],
            ["permission_roles.id"],
            name="permission_role_includes_included_role_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["permission_roles.id"],
            name="permission_role_includes_role_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("role_id", "included_role_id"),
        comment="A composition edge: one role including another's patterns.",
    )

    op.create_table(
        "permission_grants",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("pattern", sa.Text(), nullable=False),
        sa.Column("effect", sa.SmallInteger(), nullable=False),
        sa.Column("granted_by_account_id", sa.Integer(), nullable=False),
        sa.Column("subject_account_id", sa.Integer(), nullable=True),
        sa.Column("subject_role_id", sa.BigInteger(), nullable=True),
        sa.Column("subject_guild_id", sa.BigInteger(), nullable=True),
        sa.Column("scope_guild_id", sa.BigInteger(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("granted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.CheckConstraint("effect IN (1, -1, -2)", name="permission_grants_effect_check"),
        sa.CheckConstraint(_ONE_SUBJECT, name="permission_grants_one_subject"),
        sa.CheckConstraint(_ROLE_SUBJECT_HAS_GUILD, name="permission_grants_role_subject_has_guild"),
        sa.CheckConstraint(_ROLE_SUBJECT_STAYS_HOME, name="permission_grants_role_subject_stays_home"),
        sa.ForeignKeyConstraint(
            ["granted_by_account_id"],
            ["accounts.id"],
            name="permission_grants_granted_by_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["subject_account_id"],
            ["accounts.id"],
            name="permission_grants_subject_account_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        comment="A direct allow, deny or forbid attached to an account or a Discord role.",
    )
    op.create_index(
        "permission_grants_by_account",
        "permission_grants",
        ["subject_account_id"],
        postgresql_where=sa.text("subject_account_id IS NOT NULL"),
    )
    op.create_index(
        "permission_grants_by_role",
        "permission_grants",
        ["subject_guild_id", "subject_role_id"],
        postgresql_where=sa.text("subject_role_id IS NOT NULL"),
    )
    op.execute(
        """
        CREATE UNIQUE INDEX permission_grants_account_unique
        ON public.permission_grants (subject_account_id, pattern, scope_guild_id)
        NULLS NOT DISTINCT
        WHERE subject_account_id IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX permission_grants_role_unique
        ON public.permission_grants (subject_role_id, pattern, scope_guild_id)
        NULLS NOT DISTINCT
        WHERE subject_role_id IS NOT NULL
        """
    )

    op.create_table(
        "permission_role_assignments",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("granted_by_account_id", sa.Integer(), nullable=False),
        sa.Column("subject_account_id", sa.Integer(), nullable=True),
        sa.Column("subject_role_id", sa.BigInteger(), nullable=True),
        sa.Column("subject_guild_id", sa.BigInteger(), nullable=True),
        sa.Column("scope_guild_id", sa.BigInteger(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("granted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.CheckConstraint(_ONE_SUBJECT, name="permission_role_assignments_one_subject"),
        sa.CheckConstraint(_ROLE_SUBJECT_HAS_GUILD, name="permission_role_assignments_role_subject_has_guild"),
        sa.CheckConstraint(_ROLE_SUBJECT_STAYS_HOME, name="permission_role_assignments_role_subject_stays_home"),
        sa.ForeignKeyConstraint(
            ["granted_by_account_id"],
            ["accounts.id"],
            name="permission_role_assignments_granted_by_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["permission_roles.id"],
            name="permission_role_assignments_role_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["subject_account_id"],
            ["accounts.id"],
            name="permission_role_assignments_subject_account_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        comment="A role held by an account or by everyone with a Discord role.",
    )
    op.create_index(
        "permission_role_assignments_by_account",
        "permission_role_assignments",
        ["subject_account_id"],
        postgresql_where=sa.text("subject_account_id IS NOT NULL"),
    )
    op.create_index(
        "permission_role_assignments_by_role",
        "permission_role_assignments",
        ["subject_guild_id", "subject_role_id"],
        postgresql_where=sa.text("subject_role_id IS NOT NULL"),
    )
    op.execute(
        """
        CREATE UNIQUE INDEX permission_role_assignments_account_unique
        ON public.permission_role_assignments (subject_account_id, role_id, scope_guild_id)
        NULLS NOT DISTINCT
        WHERE subject_account_id IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX permission_role_assignments_role_unique
        ON public.permission_role_assignments (subject_role_id, role_id, scope_guild_id)
        NULLS NOT DISTINCT
        WHERE subject_role_id IS NOT NULL
        """
    )

    op.create_table(
        "permission_audit_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("actor_account_id", sa.Integer(), nullable=True),
        sa.Column("subject_kind", sa.Text(), nullable=True),
        sa.Column("subject_id", sa.BigInteger(), nullable=True),
        sa.Column("subject_guild_id", sa.BigInteger(), nullable=True),
        sa.Column("pattern", sa.Text(), nullable=True),
        sa.Column("role_id", sa.Integer(), nullable=True),
        sa.Column("scope_guild_id", sa.BigInteger(), nullable=True),
        sa.Column("effect", sa.SmallInteger(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_account_id"],
            ["accounts.id"],
            name="permission_audit_log_actor_fkey",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        comment="An append-only record of one permission mutation.",
    )
    op.create_index("permission_audit_log_recent", "permission_audit_log", ["at"])
    op.create_index("permission_audit_log_by_subject", "permission_audit_log", ["subject_kind", "subject_id"])

    op.create_table(
        "permission_epoch",
        sa.Column("id", sa.SmallInteger(), nullable=False),
        sa.Column("version", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("id = 1", name="permission_epoch_singleton"),
        sa.PrimaryKeyConstraint("id"),
        comment="A single counter bumped by any permission write.",
    )
    # Seeded before the trigger exists, so the first bump has a row to update.
    op.execute("INSERT INTO public.permission_epoch (id, version) VALUES (1, 1)")

    for entity in _selected_entities(PGFunction, _FUNCTION_NAMES):
        op.execute(entity.to_sql_statement_create())
    for entity in _selected_entities(PGTrigger, _TRIGGER_NAMES):
        op.execute(entity.to_sql_statement_create())
    op.execute(_LEGACY_TIER_TRIGGER)

    roles = sa.table(
        "permission_roles",
        sa.column("slug", sa.Text),
        sa.column("name", sa.Text),
        sa.column("description", sa.Text),
        sa.column("rank", sa.Integer),
        sa.column("protected", sa.Boolean),
        sa.column("builtin_key", sa.Text),
    )
    op.bulk_insert(
        roles,
        [
            {
                "slug": slug,
                "name": name,
                "description": description,
                "rank": rank,
                "protected": True,
                "builtin_key": slug,
            }
            for slug, name, description, rank in _BUILTIN_ROLES
        ],
    )


def downgrade() -> None:
    """Drop the RBAC tables and the epoch trigger."""
    op.execute("DROP TRIGGER IF EXISTS global_administrators_bump_epoch ON public.global_administrators")
    for entity in reversed(_selected_entities(PGTrigger, _TRIGGER_NAMES)):
        op.execute(entity.to_sql_statement_drop())
    for entity in reversed(_selected_entities(PGFunction, _FUNCTION_NAMES)):
        op.execute(entity.to_sql_statement_drop())

    op.drop_table("permission_epoch")
    op.drop_table("permission_audit_log")
    op.drop_table("permission_role_assignments")
    op.drop_table("permission_grants")
    op.drop_table("permission_role_includes")
    op.drop_table("permission_role_patterns")
    op.drop_table("permission_roles")


EntityT = TypeVar("EntityT", bound=ReplaceableEntity)


def _selected_entities(entity_type: type[EntityT], names: set[str]) -> list[EntityT]:
    """The named entities from the shipped definitions, in declaration order."""
    selected = [
        entity
        for entity in alembic_util_entities()
        if isinstance(entity, entity_type) and entity.signature.split("(")[0] in names
    ]
    if len(selected) != len(names):
        found = {entity.signature.split("(")[0] for entity in selected}
        msg = f"missing {entity_type.__name__} definitions: {sorted(names - found)}"
        raise RuntimeError(msg)
    return selected
