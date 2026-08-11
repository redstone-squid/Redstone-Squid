"""SQLAlchemy models for CLI device enrollment and short-lived sessions."""

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, LargeBinary, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from whenever import Instant

from squid.persistence.base import Base
from squid.persistence.types import InstantUTC


class CliDeviceEnrollmentRecord(Base, kw_only=True):
    """A browser-approved enrollment storing only digests of bearer codes."""

    __tablename__ = "cli_device_enrollments"
    __table_args__ = (
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
    user_code_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    public_key: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    client_instance_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[Instant] = mapped_column(InstantUTC(), nullable=False)
    expires_at: Mapped[Instant] = mapped_column(InstantUTC(), nullable=False)
    approved_by_account_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("accounts.id", name="cli_device_enrollments_approved_account_id_fkey", ondelete="CASCADE"),
        default=None,
    )
    approved_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    exchanged_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    revoked_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)


class CliDeviceRecord(Base, kw_only=True):
    """An account-owned Ed25519 CLI device."""

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
    public_key: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    client_instance_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )
    last_used_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )
    revoked_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)


class CliSessionChallengeRecord(Base, kw_only=True):
    """A one-time device proof nonce stored only as a digest."""

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
    created_at: Mapped[Instant] = mapped_column(InstantUTC(), nullable=False)
    expires_at: Mapped[Instant] = mapped_column(InstantUTC(), nullable=False)
    consumed_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)


class CliSessionRecord(Base, kw_only=True):
    """A short-lived CLI bearer session storing only its token digest."""

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
    issued_at: Mapped[Instant] = mapped_column(InstantUTC(), nullable=False)
    expires_at: Mapped[Instant] = mapped_column(InstantUTC(), nullable=False)
    last_seen_at: Mapped[Instant] = mapped_column(InstantUTC(), nullable=False)
    revoked_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
