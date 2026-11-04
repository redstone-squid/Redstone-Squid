"""Persist permission patterns, assignments, and their audit trail.

Permission roles are Squid's named bundles of patterns, distinct from Discord
roles. Assignments give a permission role to an account or a Discord role; grants
attach an allow, deny, or forbid pattern directly to either kind of subject.

Patterns are expanded against the current catalogue when read. Existing wildcard
grants therefore cover matching nodes added in later releases.
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Identity,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from whenever import Instant

from squid.persistence.base import Base
from squid.persistence.types import InstantUTC, now

_EFFECT_VALUES = "effect IN (1, -1, -2)"
"""Allow, deny, forbid. See `squid.permissions.domain.models.Effect`."""

_ONE_SUBJECT = "num_nonnulls(subject_account_id, subject_role_id) = 1"
"""A rule is attached to an account or to a Discord role, never both or neither."""

_ROLE_SUBJECT_HAS_GUILD = "subject_role_id IS NULL OR subject_guild_id IS NOT NULL"
"""A Discord role only means anything alongside the guild it lives in."""

_ROLE_SUBJECT_STAYS_HOME = "subject_role_id IS NULL OR scope_guild_id IS NULL OR scope_guild_id = subject_guild_id"
"""Prevent a Discord role from receiving a grant scoped to a different guild."""


class PermissionRole(Base, kw_only=True):
    """A named bundle of permission patterns and management settings.

    Built-in pattern lists live in code. Stored patterns supplement those lists,
    while this row supplies the assignment target, rank, and display metadata.
    """

    __tablename__ = "permission_roles"
    __table_args__ = (
        Index("permission_roles_created_by_idx", "created_by_account_id"),
        UniqueConstraint(
            "guild_id", "slug", name="permission_roles_guild_slug_key", postgresql_nulls_not_distinct=True
        ),
        UniqueConstraint("builtin_key", name="permission_roles_builtin_key_key"),
        CheckConstraint("builtin_key IS NULL OR guild_id IS NULL", name="permission_roles_builtin_is_global"),
        CheckConstraint("slug ~ '^[a-z][a-z0-9-]{1,31}$'", name="permission_roles_slug_format"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True, default=None)
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    """Stable handle used in commands, unique within the owning guild."""
    name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    """Display name."""
    description: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    guild_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)
    """The guild owning this role, or NULL for a global one."""
    builtin_key: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    """Identifies a role whose patterns are defined in code rather than here."""
    rank: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    """Management rank used to decide who may edit the role. It does not affect permission resolution."""
    protected: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)
    """Refuses structural edits from anyone but the bot owner."""
    created_by_account_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("accounts.id", name="permission_roles_created_by_fkey", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )

    includes: tuple[str, ...] = ()
    excludes: tuple[str, ...] = ()
    includes_roles: tuple[int, ...] = ()
    """Stored patterns and included roles, loaded in batches by the repository."""


class PermissionRolePattern(Base, kw_only=True):
    """One included or excluded pattern in a role.

    Exclusions remove matching nodes from this role's contribution without denying
    those nodes when another role or direct rule grants them.
    """

    __tablename__ = "permission_role_patterns"
    __table_args__ = (
        Index("permission_role_patterns_added_by_idx", "added_by_account_id"),
        CheckConstraint("mode IN (1, -1)", name="permission_role_patterns_mode_check"),
    )

    role_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("permission_roles.id", name="permission_role_patterns_role_id_fkey", ondelete="CASCADE"),
        primary_key=True,
    )
    pattern: Mapped[str] = mapped_column(Text, primary_key=True)
    """A node name, a `*`/`**` wildcard, or an `@tag` selector."""
    mode: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    """1 to include or -1 to exclude; a role stores one mode for each pattern."""
    added_by_account_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("accounts.id", name="permission_role_patterns_added_by_fkey", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    added_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )


class PermissionRoleInclude(Base, kw_only=True):
    """A composition edge: one role including another's patterns."""

    __tablename__ = "permission_role_includes"
    __table_args__ = (
        Index("permission_role_includes_added_by_idx", "added_by_account_id"),
        Index("permission_role_includes_included_idx", "included_role_id"),
        CheckConstraint("role_id <> included_role_id", name="permission_role_includes_no_self_include"),
    )

    role_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("permission_roles.id", name="permission_role_includes_role_id_fkey", ondelete="CASCADE"),
        primary_key=True,
    )
    included_role_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("permission_roles.id", name="permission_role_includes_included_role_id_fkey", ondelete="CASCADE"),
        primary_key=True,
    )
    added_by_account_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("accounts.id", name="permission_role_includes_added_by_fkey", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    added_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )


class PermissionGrant(Base, kw_only=True):
    """A direct allow, deny or forbid attached to an account or a Discord role."""

    __tablename__ = "permission_grants"
    __table_args__ = (
        Index("permission_grants_granted_by_idx", "granted_by_account_id"),
        CheckConstraint(_ONE_SUBJECT, name="permission_grants_one_subject"),
        CheckConstraint(_ROLE_SUBJECT_HAS_GUILD, name="permission_grants_role_subject_has_guild"),
        CheckConstraint(_ROLE_SUBJECT_STAYS_HOME, name="permission_grants_role_subject_stays_home"),
        CheckConstraint(_EFFECT_VALUES, name="permission_grants_effect_check"),
        Index(
            "permission_grants_account_unique",
            "subject_account_id",
            "pattern",
            "scope_guild_id",
            unique=True,
            postgresql_nulls_not_distinct=True,
            postgresql_where=text("subject_account_id IS NOT NULL"),
        ),
        Index(
            "permission_grants_role_unique",
            "subject_role_id",
            "pattern",
            "scope_guild_id",
            unique=True,
            postgresql_nulls_not_distinct=True,
            postgresql_where=text("subject_role_id IS NOT NULL"),
        ),
        Index(
            "permission_grants_by_account",
            "subject_account_id",
            postgresql_where=text("subject_account_id IS NOT NULL"),
        ),
        Index(
            "permission_grants_by_role",
            "subject_guild_id",
            "subject_role_id",
            postgresql_where=text("subject_role_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, default=None)
    pattern: Mapped[str] = mapped_column(Text, nullable=False)
    effect: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    granted_by_account_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("accounts.id", name="permission_grants_granted_by_fkey", ondelete="RESTRICT"),
        nullable=True,
        default=None,
    )
    """Account that issued the grant, or None for a system operation."""
    subject_account_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("accounts.id", name="permission_grants_subject_account_fkey", ondelete="CASCADE"),
        nullable=True,
        default=None,
    )
    subject_role_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)
    """A Discord role snowflake, when the rule is attached to a role."""
    subject_guild_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)
    """The guild the subject Discord role lives in."""
    scope_guild_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)
    """Guild where the grant applies, or None for all guilds. Guild-scoped grants cannot satisfy global nodes."""
    expires_at: Mapped[Instant | None] = mapped_column(InstantUTC(), nullable=True, default=None)
    granted_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)


class PermissionRoleAssignment(Base, kw_only=True):
    """A role held by an account or by everyone with a Discord role."""

    __tablename__ = "permission_role_assignments"
    __table_args__ = (
        Index("permission_role_assignments_granted_by_idx", "granted_by_account_id"),
        Index("permission_role_assignments_role_idx", "role_id"),
        CheckConstraint(_ONE_SUBJECT, name="permission_role_assignments_one_subject"),
        CheckConstraint(_ROLE_SUBJECT_HAS_GUILD, name="permission_role_assignments_role_subject_has_guild"),
        CheckConstraint(_ROLE_SUBJECT_STAYS_HOME, name="permission_role_assignments_role_subject_stays_home"),
        Index(
            "permission_role_assignments_account_unique",
            "subject_account_id",
            "role_id",
            "scope_guild_id",
            unique=True,
            postgresql_nulls_not_distinct=True,
            postgresql_where=text("subject_account_id IS NOT NULL"),
        ),
        Index(
            "permission_role_assignments_role_unique",
            "subject_role_id",
            "role_id",
            "scope_guild_id",
            unique=True,
            postgresql_nulls_not_distinct=True,
            postgresql_where=text("subject_role_id IS NOT NULL"),
        ),
        Index(
            "permission_role_assignments_by_account",
            "subject_account_id",
            postgresql_where=text("subject_account_id IS NOT NULL"),
        ),
        Index(
            "permission_role_assignments_by_role",
            "subject_guild_id",
            "subject_role_id",
            postgresql_where=text("subject_role_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, default=None)
    role_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("permission_roles.id", name="permission_role_assignments_role_id_fkey", ondelete="CASCADE"),
        nullable=False,
    )
    granted_by_account_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("accounts.id", name="permission_role_assignments_granted_by_fkey", ondelete="RESTRICT"),
        nullable=True,
        default=None,
    )
    """Account that issued the assignment, or None for a system operation."""
    subject_account_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("accounts.id", name="permission_role_assignments_subject_account_fkey", ondelete="CASCADE"),
        nullable=True,
        default=None,
    )
    subject_role_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)
    subject_guild_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)
    scope_guild_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)
    expires_at: Mapped[Instant | None] = mapped_column(InstantUTC(), nullable=True, default=None)
    granted_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

    role_slug: str = ""
    """Role name resolved for administrative listings."""


class PermissionAuditEntry(Base, kw_only=True):
    """An append-only record of one permission mutation.

    Written in the same transaction as the change it describes. The repository
    exposes no update or delete path for this table.
    """

    __tablename__ = "permission_audit_log"
    __table_args__ = (
        Index("permission_audit_log_actor_idx", "actor_account_id"),
        Index("permission_audit_log_recent", "at"),
        Index("permission_audit_log_by_subject", "subject_kind", "subject_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, default=None)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    """Mutation name, such as `grant`, `revoke`, `role_create`, or `role_include`."""
    actor_account_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("accounts.id", name="permission_audit_log_actor_fkey", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    subject_kind: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    subject_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)
    subject_guild_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)
    pattern: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    role_id: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    scope_guild_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)
    effect: Mapped[int | None] = mapped_column(SmallInteger, nullable=True, default=None)
    expires_at: Mapped[Instant | None] = mapped_column(InstantUTC(), nullable=True, default=None)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    """Reason for the change; the service requires one for a forbid grant."""
    details: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True, default=None)
    at: Mapped[Instant] = mapped_column(InstantUTC(), nullable=False, server_default=func.now(), default_factory=now)


class PermissionEpoch(Base, kw_only=True):
    """A shared counter used to invalidate permission caches.

    Database triggers increment the counter and send `NOTIFY squid_permissions`
    after permission changes. Watchers poll the counter and use notifications
    to refresh sooner.
    """

    __tablename__ = "permission_epoch"
    __table_args__ = (CheckConstraint("id = 1", name="permission_epoch_singleton"),)

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=1)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"), default=1)
    updated_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
