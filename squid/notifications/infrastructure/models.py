"""SQLAlchemy notification preference, inbox, and delivery models."""

import uuid

from sqlalchemy import (
    UUID,
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from whenever import Instant

from squid.notifications.domain import NotificationKind, SubscriptionKind
from squid.persistence.base import Base
from squid.persistence.types import InstantUTC, StrEnumText, now


class NotificationProfile(Base, kw_only=True):
    """Independent notification channel preferences.

    Carries no consent receipt: notifications are covered by the one privacy notice, whose
    receipt lives on `accounts`. A row here means "these switches", not "this person agreed".
    """

    __tablename__ = "notification_profiles"

    account_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("accounts.id", name="notification_profiles_account_id_fkey", ondelete="CASCADE"),
        primary_key=True,
    )
    web_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)
    dm_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)
    dm_suspended_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
    updated_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )


class NotificationSubscriptionRecord(Base, kw_only=True):
    """A creator, exact record, or structured record-filter subscription."""

    __tablename__ = "notification_subscriptions"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('creator', 'record', 'record_filter')",
            name="notification_subscriptions_kind_check",
        ),
        CheckConstraint(
            "(kind = 'record_filter') = (filter IS NOT NULL)",
            name="notification_subscriptions_filter_complete",
        ),
        CheckConstraint(
            "(kind IN ('creator', 'record')) = (subject_id IS NOT NULL)",
            name="notification_subscriptions_subject_complete",
        ),
        Index("notification_subscriptions_subject_idx", "kind", "subject_id"),
        Index("notification_subscriptions_account_idx", "account_id", "created_at"),
        Index(
            "notification_subscriptions_exact_key",
            "account_id",
            "kind",
            "subject_id",
            unique=True,
            postgresql_where=text("enabled AND subject_id IS NOT NULL"),
        ),
        Index(
            "notification_subscriptions_filter_key",
            "account_id",
            "kind",
            "filter",
            unique=True,
            postgresql_where=text("enabled AND filter IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    account_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("accounts.id", name="notification_subscriptions_account_id_fkey", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[SubscriptionKind] = mapped_column(StrEnumText(SubscriptionKind), nullable=False)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    filter: Mapped[dict[str, object] | None] = mapped_column(JSONB(none_as_null=True), default=None)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"), default=True)
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )


class NotificationRecord(Base, kw_only=True):
    """An idempotently materialized user notification."""

    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('build_confirmed', 'build_denied', 'creator_build_confirmed', "
            "'record_gained', 'staff_build_submitted')",
            name="notifications_kind_check",
        ),
        UniqueConstraint("source_key", name="notifications_source_key_key"),
        Index("notifications_account_inbox_idx", "account_id", "id"),
        Index("notifications_created_idx", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    account_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("accounts.id", name="notifications_account_id_fkey", ondelete="CASCADE"),
        nullable=False,
    )
    event_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("domain_events.id", name="notifications_event_id_fkey", ondelete="CASCADE"),
        nullable=False,
    )
    source_key: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[NotificationKind] = mapped_column(StrEnumText(NotificationKind), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default_factory=dict
    )
    web_visible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
    read_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)


class NotificationDeliveryRecord(Base, kw_only=True):
    """A durable at-least-once Discord DM delivery attempt."""

    __tablename__ = "notification_deliveries"
    __table_args__ = (
        Index("notification_deliveries_account_idx", "account_id"),
        UniqueConstraint("notification_id", name="notification_deliveries_notification_id_key"),
        CheckConstraint("attempts >= 0", name="notification_deliveries_attempts_nonnegative"),
        CheckConstraint("generation > 0", name="notification_deliveries_generation_positive"),
        CheckConstraint(
            "(claimed_at IS NULL) = (claim_token IS NULL)",
            name="notification_deliveries_claim_complete",
        ),
        Index(
            "notification_deliveries_ready_idx",
            "available_at",
            postgresql_where=text("claimed_at IS NULL AND dead_at IS NULL AND sent_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    notification_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("notifications.id", name="notification_deliveries_notification_id_fkey", ondelete="CASCADE"),
        nullable=False,
    )
    account_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("accounts.id", name="notification_deliveries_account_id_fkey", ondelete="CASCADE"),
        nullable=False,
    )
    generation: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"), default=1)
    nonce: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, server_default=text("gen_random_uuid()"), default_factory=uuid.uuid4
    )
    available_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
    claimed_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    claim_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    sent_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    dead_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
