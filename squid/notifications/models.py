"""SQLAlchemy notification preference, inbox, and delivery models."""

import uuid
from typing import override

from sqlalchemy import (
    UUID,
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator
from whenever import Instant

from squid.notifications.domain import NotificationKind, RecordSubscriptionFilter, SubscriptionKind
from squid.persistence.base import Base
from squid.persistence.types import InstantUTC, StrEnumText, now


class RecordFilterJSON(TypeDecorator[RecordSubscriptionFilter]):
    """Convert subscription filters to JSONB and validate them when reading."""

    impl = JSONB(none_as_null=True)
    cache_ok = True

    @override
    def process_bind_param(self, value: RecordSubscriptionFilter | None, dialect: Dialect) -> dict[str, object] | None:
        return None if value is None else value.as_dict()

    @override
    def process_result_value(
        self, value: dict[str, object] | None, dialect: Dialect
    ) -> RecordSubscriptionFilter | None:
        return None if value is None else RecordSubscriptionFilter.from_dict(value)


class NotificationProfile(Base, kw_only=True):
    """Channel preferences for an account. Consent is recorded on the account itself."""

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


class NotificationSubscription(Base, kw_only=True):
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

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, default=None)
    account_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("accounts.id", name="notification_subscriptions_account_id_fkey", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[SubscriptionKind] = mapped_column(StrEnumText(SubscriptionKind), nullable=False)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    record_filter: Mapped[RecordSubscriptionFilter | None] = mapped_column("filter", RecordFilterJSON(), default=None)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"), default=True)
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )


class InboxNotification(Base, kw_only=True):
    """An event-derived message for one recipient, shared by the web inbox and DM delivery."""

    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('build_confirmed', 'build_denied', 'creator_build_confirmed', "
            "'record_gained', 'staff_build_submitted')",
            name="notifications_kind_check",
        ),
        UniqueConstraint("source_key", name="notifications_source_key_key"),
        UniqueConstraint("id", "account_id", name="notifications_id_account_key"),
        Index("notifications_account_inbox_idx", "account_id", "id"),
        Index("notifications_created_idx", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, default=None)
    account_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("accounts.id", name="notifications_account_id_fkey", ondelete="CASCADE"),
        nullable=False,
        default=None,
    )
    event_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("domain_events.id", name="notifications_event_id_fkey", ondelete="CASCADE"),
        nullable=False,
        default=None,
    )
    source_key: Mapped[str] = mapped_column(Text, nullable=False, default="")
    kind: Mapped[NotificationKind] = mapped_column(StrEnumText(NotificationKind), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default_factory=dict
    )
    web_visible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
    read_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)


class NotificationDeliveryRecord(Base, kw_only=True):
    """A queued Discord DM retained across retries and after successful or terminal delivery."""

    __tablename__ = "notification_deliveries"
    __table_args__ = (
        ForeignKeyConstraint(
            ("notification_id", "account_id"),
            ("notifications.id", "notifications.account_id"),
            name="notification_deliveries_notification_owner_fkey",
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
        ),
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

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, default=None)
    notification_id: Mapped[int] = mapped_column(
        BigInteger,
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
