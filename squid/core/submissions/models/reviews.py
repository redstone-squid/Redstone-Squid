"""Durable inputs to new-build and edit reviews."""

from uuid import UUID, uuid4

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from whenever import Instant

from squid.core.persistence.base import Base
from squid.core.persistence.types import InstantUTC


class BuildSubmission(Base, kw_only=True):
    """A submitted version, retained independently of its draft and voting session."""

    __tablename__ = "build_submissions"
    __table_args__ = (
        CheckConstraint(
            "(edit_vote_id IS NULL AND base_revision IS NULL) OR "
            "(finalization_job_id IS NULL AND edit_vote_id IS NOT NULL AND base_revision IS NOT NULL AND base_revision > 0)",
            name="build_submissions_origin_check",
        ),
        Index(
            "build_submissions_new_build_idx", "build_id", unique=True, postgresql_where=text("edit_vote_id IS NULL")
        ),
        Index("build_submissions_build_idx", "build_id"),
        Index("build_submissions_submitter_idx", "submitter_account_id", "submitted_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"), default_factory=uuid4)
    build_id: Mapped[int] = mapped_column(BigInteger)
    submitter_account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    submitted_at: Mapped[Instant] = mapped_column(InstantUTC(), server_default=func.now(), default_factory=Instant.now)
    finalization_job_id: Mapped[UUID | None] = mapped_column(unique=True, default=None)
    edit_vote_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, default=None)
    base_revision: Mapped[int | None] = mapped_column(BigInteger, default=None)
    content: Mapped[dict[str, object]] = mapped_column(JSONB)
    decision: Mapped[str] = mapped_column(Text, default="pending", server_default=text("'pending'"))
    decided_revision: Mapped[int | None] = mapped_column(BigInteger, default=None)
    feedback: Mapped[str | None] = mapped_column(Text, default=None)
