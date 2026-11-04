"""Retained inference inputs, worker claims, and candidate results."""

from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as SQLUUID
from sqlalchemy.orm import Mapped, mapped_column
from whenever import Instant

from squid.core.errors import JSONValue
from squid.persistence.base import Base
from squid.persistence.types import InstantUTC


class SubmissionInferenceRun(Base, kw_only=True):
    """Private inference inputs, candidate results, and the current expiring worker claim."""

    __tablename__ = "submission_inference_runs"
    __table_args__ = (
        CheckConstraint("state IN ('processing', 'failed', 'completed')", name="submission_inference_runs_state_check"),
    )
    id: Mapped[UUID] = mapped_column(SQLUUID(as_uuid=True), primary_key=True)
    owner_account_id: Mapped[int] = mapped_column(Integer, ForeignKey("accounts.id", ondelete="RESTRICT"), index=True)
    inputs: Mapped[dict[str, JSONValue]] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[Instant] = mapped_column(InstantUTC(), index=True)
    claim_token: Mapped[UUID | None] = mapped_column(SQLUUID(as_uuid=True))
    claim_expires_at: Mapped[Instant | None] = mapped_column(InstantUTC())
    candidates: Mapped[list[dict[str, JSONValue]]] = mapped_column(JSONB, default_factory=list)
