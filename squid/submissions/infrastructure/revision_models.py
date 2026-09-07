"""Persisted recalculation candidates and their explicit approval receipts."""

from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Integer, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as SQLUUID
from sqlalchemy.orm import Mapped, mapped_column
from whenever import Instant

from squid.core.errors import JSONValue
from squid.persistence.base import Base
from squid.persistence.types import InstantUTC, now


class SubmissionRevisionProposal(Base, kw_only=True):
    """A retained inference candidate, review snapshot, and atomic build revision receipt."""

    __tablename__ = "submission_revision_proposals"
    __table_args__ = (
        CheckConstraint(
            "(build_id IS NULL) = (expected_revision IS NULL)", name="submission_revision_proposals_target_check"
        ),
        CheckConstraint(
            "(approved_by_account_id IS NULL) = (applied_revision IS NULL)",
            name="submission_revision_proposals_receipt_check",
        ),
        CheckConstraint(
            "expected_revision IS NULL OR expected_revision >= 0", name="submission_revision_proposals_revision_check"
        ),
        Index("submission_revision_proposals_run_idx", "run_id"),
        Index("submission_revision_proposals_owner_idx", "owner_account_id"),
        Index("submission_revision_proposals_actor_idx", "requested_by_account_id"),
        Index("submission_revision_proposals_approver_idx", "approved_by_account_id"),
        Index("submission_revision_proposals_build_idx", "build_id"),
        Index("submission_revision_proposals_source_idx", "source_message_id"),
        Index("submission_revision_proposals_expiry_idx", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(SQLUUID(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(SQLUUID(as_uuid=True))
    owner_account_id: Mapped[int] = mapped_column(Integer, ForeignKey("accounts.id", ondelete="RESTRICT"))
    requested_by_account_id: Mapped[int] = mapped_column(Integer, ForeignKey("accounts.id", ondelete="RESTRICT"))
    source_message_id: Mapped[int] = mapped_column(BigInteger)
    facts: Mapped[dict[str, JSONValue]] = mapped_column(JSONB)
    expires_at: Mapped[Instant] = mapped_column(InstantUTC())
    build_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("builds.id", ondelete="RESTRICT"), default=None)
    expected_revision: Mapped[int | None] = mapped_column(Integer, default=None)
    before: Mapped[dict[str, JSONValue]] = mapped_column(JSONB, default_factory=dict)
    after: Mapped[dict[str, JSONValue]] = mapped_column(JSONB, default_factory=dict)
    approved_by_account_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("accounts.id", ondelete="RESTRICT"), default=None
    )
    applied_revision: Mapped[int | None] = mapped_column(Integer, default=None)
    created_at: Mapped[Instant] = mapped_column(InstantUTC(), default_factory=now, server_default=func.now())
