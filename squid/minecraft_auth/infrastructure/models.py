"""SQLAlchemy models for Minecraft installation and player authorization."""

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from whenever import Instant

from squid.persistence.base import Base
from squid.persistence.types import InstantUTC


class PaperInstallationRecord(Base, kw_only=True):
    """An account-owned Paper installation with a non-recoverable credential."""

    __tablename__ = "minecraft_paper_installations"
    __table_args__ = (
        CheckConstraint("char_length(label) BETWEEN 1 AND 80", name="minecraft_paper_installations_label_length"),
        CheckConstraint("octet_length(secret_hash) = 32", name="minecraft_paper_installations_secret_hash_length"),
        CheckConstraint("credential_version >= 1", name="minecraft_paper_installations_version_positive"),
        CheckConstraint(
            "public_display_name IS NULL OR char_length(public_display_name) BETWEEN 1 AND 80",
            name="minecraft_paper_installations_display_name_length",
        ),
        CheckConstraint(
            "public_address IS NULL OR char_length(public_address) BETWEEN 1 AND 255",
            name="minecraft_paper_installations_address_length",
        ),
        CheckConstraint(
            "public_description IS NULL OR char_length(public_description) BETWEEN 1 AND 500",
            name="minecraft_paper_installations_description_length",
        ),
        CheckConstraint(
            "public_website_url IS NULL OR char_length(public_website_url) BETWEEN 1 AND 2048",
            name="minecraft_paper_installations_website_length",
        ),
        CheckConstraint(
            "NOT sponsor_opt_in OR public_profile_enabled",
            name="minecraft_paper_installations_sponsor_requires_public",
        ),
        Index("minecraft_paper_installations_owner_idx", "owner_account_id", "created_at"),
        Index(
            "minecraft_paper_installations_public_idx",
            "created_at",
            postgresql_where=text("public_profile_enabled AND revoked_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    owner_account_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("accounts.id", name="minecraft_paper_installations_owner_account_id_fkey", ondelete="CASCADE"),
        nullable=False,
    )
    label: Mapped[str] = mapped_column(Text, nullable=False)
    secret_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    credential_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"), default=1)
    public_profile_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    public_display_name: Mapped[str | None] = mapped_column(Text, default=None)
    public_address: Mapped[str | None] = mapped_column(Text, default=None)
    public_description: Mapped[str | None] = mapped_column(Text, default=None)
    public_website_url: Mapped[str | None] = mapped_column(Text, default=None)
    sponsor_opt_in: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )
    rotated_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    revoked_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)


class PlayerChallengeRecord(Base, kw_only=True):
    """A short-lived device flow storing only hashes of both bearer codes."""

    __tablename__ = "minecraft_player_challenges"
    __table_args__ = (
        UniqueConstraint("device_code_hash", name="minecraft_player_challenges_device_code_hash_key"),
        UniqueConstraint("user_code_hash", name="minecraft_player_challenges_user_code_hash_key"),
        CheckConstraint("octet_length(device_code_hash) = 32", name="minecraft_player_challenges_device_hash_length"),
        CheckConstraint("octet_length(user_code_hash) = 32", name="minecraft_player_challenges_user_hash_length"),
        CheckConstraint("origin IN ('paper', 'fabric')", name="minecraft_player_challenges_origin_check"),
        CheckConstraint("expires_at > created_at", name="minecraft_player_challenges_expiry_after_creation"),
        CheckConstraint(
            "(approved_by_account_id IS NULL) = (approved_at IS NULL)",
            name="minecraft_player_challenges_approval_complete",
        ),
        CheckConstraint(
            "exchanged_at IS NULL OR approved_at IS NOT NULL",
            name="minecraft_player_challenges_exchange_requires_approval",
        ),
        CheckConstraint(
            "(origin = 'paper' AND installation_id IS NOT NULL AND installation_credential_version IS NOT NULL "
            "AND pkce_s256_challenge IS NULL) OR "
            "(origin = 'fabric' AND installation_id IS NULL AND installation_credential_version IS NULL "
            "AND pkce_s256_challenge IS NOT NULL)",
            name="minecraft_player_challenges_origin_binding",
        ),
        CheckConstraint(
            "pkce_s256_challenge IS NULL OR pkce_s256_challenge ~ '^[A-Za-z0-9_-]{43}$'",
            name="minecraft_player_challenges_pkce_format",
        ),
        Index(
            "minecraft_player_challenges_active_lookup_idx",
            "origin",
            "java_uuid",
            "installation_id",
            "expires_at",
            postgresql_where=text("exchanged_at IS NULL AND revoked_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    device_code_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    user_code_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    origin: Mapped[str] = mapped_column(Text, nullable=False)
    java_uuid: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    installation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "minecraft_paper_installations.id",
            name="minecraft_player_challenges_installation_id_fkey",
            ondelete="CASCADE",
        ),
        default=None,
    )
    installation_credential_version: Mapped[int | None] = mapped_column(Integer, default=None)
    pkce_s256_challenge: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[Instant] = mapped_column(InstantUTC(), nullable=False)
    expires_at: Mapped[Instant] = mapped_column(InstantUTC(), nullable=False)
    approved_by_account_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("accounts.id", name="minecraft_player_challenges_approved_account_id_fkey", ondelete="CASCADE"),
        default=None,
    )
    approved_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    exchanged_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    revoked_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)


class PlayerGrantRecord(Base, kw_only=True):
    """A short-lived origin- and identity-bound player bearer grant."""

    __tablename__ = "minecraft_player_grants"
    __table_args__ = (
        UniqueConstraint("challenge_id", name="minecraft_player_grants_challenge_id_key"),
        UniqueConstraint("token_hash", name="minecraft_player_grants_token_hash_key"),
        CheckConstraint("octet_length(token_hash) = 32", name="minecraft_player_grants_token_hash_length"),
        CheckConstraint("origin IN ('paper', 'fabric')", name="minecraft_player_grants_origin_check"),
        CheckConstraint("expires_at > issued_at", name="minecraft_player_grants_expiry_after_issue"),
        CheckConstraint(
            "(origin = 'paper' AND installation_id IS NOT NULL AND installation_credential_version IS NOT NULL) OR "
            "(origin = 'fabric' AND installation_id IS NULL AND installation_credential_version IS NULL)",
            name="minecraft_player_grants_origin_binding",
        ),
        Index("minecraft_player_grants_account_idx", "account_id", "expires_at"),
        Index(
            "minecraft_player_grants_active_installation_idx",
            "installation_id",
            "expires_at",
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    challenge_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "minecraft_player_challenges.id",
            name="minecraft_player_grants_challenge_id_fkey",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    account_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("accounts.id", name="minecraft_player_grants_account_id_fkey", ondelete="CASCADE"),
        nullable=False,
    )
    java_uuid: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    origin: Mapped[str] = mapped_column(Text, nullable=False)
    installation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "minecraft_paper_installations.id",
            name="minecraft_player_grants_installation_id_fkey",
            ondelete="CASCADE",
        ),
        default=None,
    )
    installation_credential_version: Mapped[int | None] = mapped_column(Integer, default=None)
    issued_at: Mapped[Instant] = mapped_column(InstantUTC(), nullable=False)
    expires_at: Mapped[Instant] = mapped_column(InstantUTC(), nullable=False)
    revoked_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
