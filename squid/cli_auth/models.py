"""SQLAlchemy models for CLI device enrollment and short-lived sessions."""

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, LargeBinary, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from whenever import Instant

from squid.persistence.base import Base
from squid.persistence.types import InstantUTC, now


class CliDeviceEnrollmentRecord(Base, kw_only=True):
    """One pending CLI enrollment, spent by the exchange that turns it into a device and session."""

    __tablename__ = "cli_device_enrollments"
    __table_args__ = (
        Index("cli_device_enrollments_approved_by_idx", "approved_by_account_id"),
        UniqueConstraint("device_code_hash", name="cli_device_enrollments_device_code_hash_key"),
        UniqueConstraint("user_code_hash", name="cli_device_enrollments_user_code_hash_key"),
        CheckConstraint("octet_length(device_code_hash) = 32", name="cli_device_enrollments_device_hash_length"),
        CheckConstraint("octet_length(user_code_hash) = 32", name="cli_device_enrollments_user_hash_length"),
        CheckConstraint("octet_length(public_key) = 32", name="cli_device_enrollments_public_key_length"),
        CheckConstraint("char_length(label) BETWEEN 1 AND 80", name="cli_device_enrollments_label_length"),
        CheckConstraint("expires_at > created_at", name="cli_device_enrollments_expiry_after_creation"),
        CheckConstraint(
            "(approved_by_account_id IS NULL) = (approved_at IS NULL)",
            name="cli_device_enrollments_approval_complete",
        ),
        CheckConstraint(
            "exchanged_at IS NULL OR approved_at IS NOT NULL",
            name="cli_device_enrollments_exchange_requires_approval",
        ),
        Index(
            "cli_device_enrollments_active_client_idx",
            "client_instance_id",
            "expires_at",
            postgresql_where=text("exchanged_at IS NULL AND revoked_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    device_code_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    """Keyed digest of the code the CLI polls with; the code itself is disclosed once and never stored."""
    user_code_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    """Keyed digest of the normalized code a person types into the browser."""
    public_key: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    """Raw Ed25519 public key whose private half the exchange must prove possession of."""
    client_instance_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    """The CLI installation that started this; how many it may have live at once is bounded."""
    label: Mapped[str] = mapped_column(Text, nullable=False)
    """Device name shown on the browser approval screen."""
    created_at: Mapped[Instant] = mapped_column(InstantUTC(), nullable=False)
    expires_at: Mapped[Instant] = mapped_column(InstantUTC(), nullable=False)
    """When the enrollment stops being approvable or exchangeable."""
    approved_by_account_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("accounts.id", name="cli_device_enrollments_approved_account_id_fkey", ondelete="CASCADE"),
        default=None,
    )
    """Account that approved in the browser; null exactly while approved_at is."""
    approved_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    """When the browser approved; null exactly while approved_by_account_id is."""
    exchanged_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    """When the enrollment was spent for a device and session; it may only be spent once."""
    revoked_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    """When the enrollment was revoked; null while it is still usable."""


class CliDeviceRecord(Base, kw_only=True):
    """An account-owned Ed25519 CLI device, authorized until its owner revokes it."""

    __tablename__ = "cli_devices"
    __table_args__ = (
        UniqueConstraint("public_key", name="cli_devices_public_key_key"),
        CheckConstraint("octet_length(public_key) = 32", name="cli_devices_public_key_length"),
        CheckConstraint("char_length(label) BETWEEN 1 AND 80", name="cli_devices_label_length"),
        CheckConstraint("last_used_at >= created_at", name="cli_devices_last_used_after_creation"),
        Index("cli_devices_account_idx", "account_id", "created_at"),
        Index(
            "cli_devices_active_account_idx",
            "account_id",
            "last_used_at",
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    account_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("accounts.id", name="cli_devices_account_id_fkey", ondelete="CASCADE"),
        nullable=False,
    )
    """Owner; deleting the account deletes the device and everything beneath it."""
    public_key: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    """Raw Ed25519 public key, unique across all devices, and the identity that signs every proof."""
    client_instance_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    """Most recent CLI installation to present this key."""
    label: Mapped[str] = mapped_column(Text, nullable=False)
    """Device name its owner sees when listing or revoking devices."""
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
    last_used_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
    """Last enrollment exchange or challenge this device completed."""
    revoked_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    """When the owner revoked the device; revoking also revokes its live sessions."""


class CliSessionChallengeRecord(Base, kw_only=True):
    """One nonce a device signs to get a session, spent by the exchange that consumes it."""

    __tablename__ = "cli_session_challenges"
    __table_args__ = (
        UniqueConstraint("nonce_hash", name="cli_session_challenges_nonce_hash_key"),
        CheckConstraint("octet_length(nonce_hash) = 32", name="cli_session_challenges_nonce_hash_length"),
        CheckConstraint("expires_at > created_at", name="cli_session_challenges_expiry_after_creation"),
        CheckConstraint(
            "consumed_at IS NULL OR consumed_at >= created_at",
            name="cli_session_challenges_consumed_after_creation",
        ),
        Index(
            "cli_session_challenges_active_device_idx",
            "device_id",
            "expires_at",
            postgresql_where=text("consumed_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cli_devices.id", name="cli_session_challenges_device_id_fkey", ondelete="CASCADE"),
        nullable=False,
    )
    nonce_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    """Keyed digest of the nonce; the nonce is disclosed to the device once and never stored."""
    created_at: Mapped[Instant] = mapped_column(InstantUTC(), nullable=False)
    expires_at: Mapped[Instant] = mapped_column(InstantUTC(), nullable=False)
    """When the challenge stops being consumable; shorter-lived than an enrollment."""
    consumed_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    """When the nonce was spent for a session; null while the challenge is still outstanding."""


class CliSessionRecord(Base, kw_only=True):
    """A short-lived CLI bearer session, ended by its expiry or by revoking it or its device."""

    __tablename__ = "cli_sessions"
    __table_args__ = (
        UniqueConstraint("token_hash", name="cli_sessions_token_hash_key"),
        CheckConstraint("octet_length(token_hash) = 32", name="cli_sessions_token_hash_length"),
        CheckConstraint("expires_at > issued_at", name="cli_sessions_expiry_after_issue"),
        CheckConstraint("last_seen_at >= issued_at", name="cli_sessions_last_seen_after_issue"),
        Index("cli_sessions_device_idx", "device_id", "expires_at"),
        Index(
            "cli_sessions_active_device_idx",
            "device_id",
            "expires_at",
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cli_devices.id", name="cli_sessions_device_id_fkey", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    """Keyed digest of the bearer token, which is disclosed to the CLI once and never stored."""
    issued_at: Mapped[Instant] = mapped_column(InstantUTC(), nullable=False)
    expires_at: Mapped[Instant] = mapped_column(InstantUTC(), nullable=False)
    """When the token stops authenticating; the CLI signs a new challenge to get another."""
    last_seen_at: Mapped[Instant] = mapped_column(InstantUTC(), nullable=False)
    revoked_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    """When this session was revoked, directly or with its device; null while it is usable."""
