"""SQLAlchemy user account models."""

import uuid

from sqlalchemy import (
    UUID,
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from whenever import Instant

from squid.persistence.base import Base
from squid.persistence.types import InstantUTC
from squid.users.domain import CONSENT_CUTOFF, ClaimMethod, ClaimStatus


class User(Base):
    """An account we hold a relationship with, linking Discord and Minecraft identities."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("discord_id", name="users_discord_id_key"),
        UniqueConstraint("minecraft_uuid", name="users_minecraft_uuid_key"),
        CheckConstraint(
            "(consent_version IS NULL) = (consented_at IS NULL)",
            name="users_consent_receipt_complete",
        ),
        # Rows predating the consent notice are grandfathered by an explicit
        # cutoff rather than by fabricating a receipt for them; they are
        # re-prompted on their next `/account link`. A `NOT VALID` constraint
        # would express this more directly but SQLAlchemy reflects it as a
        # dialect option that Alembic's autogenerate cannot consume.
        CheckConstraint(
            f"minecraft_uuid IS NULL OR consent_version IS NOT NULL OR created_at < TIMESTAMPTZ '{CONSENT_CUTOFF}'",
            name="users_minecraft_link_requires_consent",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    """Internal primary key. Unrelated to the user's Discord or Minecraft identifiers."""
    ign: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    """The user's Minecraft in-game name, as of the last verification."""
    discord_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    """The user's Discord snowflake ID, if they have linked a Discord account."""
    minecraft_uuid: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    """The user's Mojang account UUID, if they have linked a Minecraft account."""
    created_at: Mapped[Instant | None] = mapped_column(InstantUTC(), server_default=func.now(), default=None)
    """When this row was first inserted."""
    consent_version: Mapped[str | None] = mapped_column(Text, default=None)
    """The privacy notice version accepted for the Minecraft link, or `None` if none is stored."""
    consented_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    """When the user accepted the privacy notice, or `None` if no link has been consented to."""


class CreatorAlias(Base):
    """A creator name credited on a build, optionally claimed by an account."""

    __tablename__ = "creator_aliases"
    __table_args__ = (
        UniqueConstraint("normalized_name", name="creator_aliases_normalized_name_key"),
        CheckConstraint(
            "(user_id IS NULL) = (claimed_at IS NULL)",
            name="creator_aliases_claim_complete",
        ),
        CheckConstraint(
            "(user_id IS NULL) = (claim_method IS NULL)",
            name="creator_aliases_claim_method_complete",
        ),
        CheckConstraint(
            "claim_method IS NULL OR claim_method IN ('verified_ign', 'staff_approved', 'migrated')",
            name="creator_aliases_claim_method_check",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    """Internal primary key, referenced by `build_creators`."""
    name: Mapped[str] = mapped_column(Text, nullable=False)
    """The creator name exactly as it was typed on a build submission."""
    normalized_name: Mapped[str] = mapped_column(Text, Computed("lower(btrim(name))", persisted=True), init=False)
    """Case-folded, trimmed form of `name`, used to deduplicate credits."""
    user_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", name="creator_aliases_user_id_fkey", ondelete="SET NULL"),
        default=None,
    )
    """The account credited with this name, or `None` while the name is unclaimed."""
    claimed_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    """When the alias was claimed, or `None` while it is unclaimed."""
    claim_method: Mapped[ClaimMethod | None] = mapped_column(Text, default=None)
    """How the alias came to be claimed, or `None` while it is unclaimed."""
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )
    """When this name was first credited on a build."""


class CreatorAliasClaim(Base):
    """A user's request to be credited under a creator alias, pending staff review."""

    __tablename__ = "creator_alias_claims"
    __table_args__ = (
        Index(
            "creator_alias_claims_one_pending_per_user",
            "alias_id",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="creator_alias_claims_status_check",
        ),
        CheckConstraint(
            "(status = 'pending') = (resolved_at IS NULL)",
            name="creator_alias_claims_resolution_complete",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    """Internal primary key, quoted to staff when they review the claim."""
    alias_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("creator_aliases.id", name="creator_alias_claims_alias_id_fkey", ondelete="CASCADE"),
        nullable=False,
    )
    """The creator name being claimed."""
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", name="creator_alias_claims_user_id_fkey", ondelete="CASCADE"),
        nullable=False,
    )
    """The account asking to be credited."""
    status: Mapped[ClaimStatus] = mapped_column(Text, nullable=False, default=ClaimStatus.PENDING)
    """Review state of the request."""
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )
    """When the request was opened."""
    resolved_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    """When staff approved or rejected the request, or `None` while it is pending."""
    resolved_by_discord_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    """The Discord ID of the staff member who resolved the request."""


class VerificationCode(Base):
    """A verification code for linking Minecraft accounts."""

    __tablename__ = "verification_codes"
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, init=False)
    minecraft_uuid: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    username: Mapped[str] = mapped_column(Text, nullable=False, default="")
    valid: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"), default=True)
    created: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )
    expires: Mapped[Instant] = mapped_column(
        InstantUTC(),
        nullable=False,
        server_default=text("(now() + '00:10:00'::interval)"),
        default_factory=lambda: Instant.now().add(minutes=10),
    )
