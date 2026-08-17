"""SQLAlchemy permission models.

Grants and role definitions store *patterns*, never expansions of them. A grant of
`build.**` written today therefore covers a node added next year with no re-grant
and no migration, and the expansion happens at read time against the live
catalogue in `squid.permissions.domain`.
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
from squid.persistence.types import InstantUTC

_EFFECT_VALUES = "effect IN (1, -1, -2)"
"""Allow, deny, forbid. See `squid.permissions.domain.models.Effect`."""

_ONE_SUBJECT = "num_nonnulls(subject_account_id, subject_role_id) = 1"
"""A rule is attached to an account or to a Discord role, never both or neither."""

_ROLE_SUBJECT_HAS_GUILD = "subject_role_id IS NULL OR subject_guild_id IS NOT NULL"
"""A Discord role only means anything alongside the guild it lives in."""

_ROLE_SUBJECT_STAYS_HOME = "subject_role_id IS NULL OR scope_guild_id IS NULL OR scope_guild_id = subject_guild_id"
"""The anti-escalation invariant, enforced in storage rather than only in code: a
Discord role from one guild can never carry authority scoped into another."""


class PermissionRole(Base, kw_only=True):
    """A named bundle of permission patterns.

    Built-in roles keep their pattern lists in code, so this row exists only to
    give assignments a foreign key; any `permission_role_patterns` rows attached
    to a built-in are additive overrides on top of the code-defined list.
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

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True, init=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    """Stable handle used in commands, unique within the owning guild."""
    name: Mapped[str] = mapped_column(Text, nullable=False)
    """Display name."""
    description: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    guild_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)
    """The guild owning this role, or NULL for a global one."""
    builtin_key: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    """Identifies a role whose patterns are defined in code rather than here."""
    rank: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    """Management hierarchy only: who may edit whom. Deliberately absent from
    permission resolution, so reordering roles can never change an authorization
    outcome. Enforced by property P10."""
    protected: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)
    """Refuses structural edits from anyone but the bot owner."""
    created_by_account_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("accounts.id", name="permission_roles_created_by_fkey", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )


class PermissionRolePattern(Base, kw_only=True):
    """One pattern a role includes or subtracts.

    Subtraction is not a deny: it withholds the pattern from *this* role's
    contribution, and any other role including it still confers it. That is
    Azure's `NotActions` semantics, and it is why a role can be written as "this
    namespace, minus its destructive members" without poisoning other grants.
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
    """1 to include, -1 to subtract. One mode per pattern, so a role cannot
    contradict itself."""
    added_by_account_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("accounts.id", name="permission_role_patterns_added_by_fkey", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    added_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
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
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
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

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    pattern: Mapped[str] = mapped_column(Text, nullable=False)
    effect: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    granted_by_account_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("accounts.id", name="permission_grants_granted_by_fkey", ondelete="RESTRICT"),
        nullable=True,
        default=None,
    )
    """Who issued this, or NULL when the system did: a migration backfill or the
    owner recovery CLI has no human grantor, and inventing one would put a
    fictional account into the audit trail."""
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
    """Where the rule applies, or NULL for everywhere. A guild-scoped rule can
    never satisfy a global node; the resolver checks that against the node's
    declared scope, so nodes added later are safe under old grants."""
    expires_at: Mapped[Instant | None] = mapped_column(InstantUTC(), nullable=True, default=None)
    granted_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
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

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
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
    """Who issued this, or NULL when the system did. See `PermissionGrant`."""
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
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)


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

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    """What happened: `grant`, `revoke`, `role_create`, `role_pattern_add`, and so on."""
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
    """Mandatory for `forbid`, enforced by the service rather than the schema so
    the column stays usable for the actions that do not need it."""
    details: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True, default=None)
    at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )


class PermissionEpoch(Base, kw_only=True):
    """A single counter bumped by any permission write.

    Three processes each hold their own rule-set cache, so a grant issued in the
    API has to become visible in the bot. A trigger bumps this row and sends
    `NOTIFY squid_permissions`; watchers treat the notification as a latency hint
    and poll this counter as the durable signal.
    """

    __tablename__ = "permission_epoch"
    __table_args__ = (CheckConstraint("id = 1", name="permission_epoch_singleton"),)

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=1)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"), default=1)
    updated_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )
