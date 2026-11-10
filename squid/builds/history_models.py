"""Immutable build states and the operations connecting them."""

from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from whenever import Instant

from squid.persistence.base import Base
from squid.persistence.types import InstantUTC


class BuildHistorySnapshot(Base, kw_only=True):
    """One observed build state, shared by adjacent history entries."""

    __tablename__ = "build_history_states"
    __table_args__ = (
        UniqueConstraint("build_id", "id"),
        CheckConstraint("build_revision > 0", name="build_history_states_revision_positive"),
        CheckConstraint("schema_version > 0", name="build_history_states_schema_positive"),
        CheckConstraint("jsonb_typeof(snapshot) = 'object'", name="build_history_states_snapshot_object"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True, init=False)
    build_id: Mapped[int] = mapped_column(BigInteger)
    captured_at: Mapped[Instant] = mapped_column(InstantUTC(), server_default=func.clock_timestamp(), init=False)
    build_revision: Mapped[int] = mapped_column(BigInteger)
    schema_version: Mapped[int] = mapped_column(SmallInteger)
    snapshot: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)


class BuildHistoryEntry(Base, kw_only=True):
    """An append-only operation whose states and actor survive live-record deletion."""

    __tablename__ = "build_edit_history"
    __table_args__ = (
        Index("build_edit_history_timeline_idx", "build_id", "id"),
        UniqueConstraint("build_id", "operation_id"),
        ForeignKeyConstraint(
            ["build_id", "before_state_id"],
            ["build_history_states.build_id", "build_history_states.id"],
            name="build_history_before_fkey",
        ),
        ForeignKeyConstraint(
            ["build_id", "after_state_id"],
            ["build_history_states.build_id", "build_history_states.id"],
            name="build_history_after_fkey",
        ),
        CheckConstraint("num_nonnulls(actor_account_id, actor_system) = 1", name="build_history_actor_check"),
        CheckConstraint("actor_account_id IS NULL OR actor_account_id > 0", name="build_history_account_positive"),
        CheckConstraint(
            "length(btrim(source)) > 0 AND (actor_system IS NULL OR length(btrim(actor_system)) > 0)",
            name="build_history_source_check",
        ),
        CheckConstraint(
            "(correlation_kind IS NULL) = (correlation_id IS NULL)", name="build_history_correlation_check"
        ),
        CheckConstraint(
            "(operation IN ('baseline', 'create') AND before_state_id IS NULL AND after_state_id IS NOT NULL) OR (operation = 'delete' AND before_state_id IS NOT NULL AND after_state_id IS NULL) OR (operation IN ('edit', 'moderation', 'recalculation', 'schematic', 'account_merge') AND before_state_id IS NOT NULL AND after_state_id IS NOT NULL)",
            name="build_history_states_check",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True, init=False)
    build_id: Mapped[int] = mapped_column(BigInteger)
    recorded_at: Mapped[Instant] = mapped_column(InstantUTC(), server_default=func.clock_timestamp(), init=False)
    operation_id: Mapped[UUID] = mapped_column(PGUUID)
    operation: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    actor_account_id: Mapped[int | None] = mapped_column(Integer)
    actor_system: Mapped[str | None] = mapped_column(Text)
    correlation_kind: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str | None] = mapped_column(Text)
    before_state_id: Mapped[int | None] = mapped_column(BigInteger)
    after_state_id: Mapped[int | None] = mapped_column(BigInteger)


class PendingBuildEdit(Base, kw_only=True):
    """Transaction-local work removed by the deferred history trigger before commit."""

    __tablename__ = "build_edit_pending"
    __table_args__ = (
        CheckConstraint(
            "(before_snapshot IS NULL) = (before_revision IS NULL)", name="build_edit_pending_before_check"
        ),
    )

    transaction_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    build_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    context: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    before_snapshot: Mapped[dict[str, JsonValue] | None] = mapped_column(JSONB, nullable=True)
    before_revision: Mapped[int | None] = mapped_column(BigInteger)
