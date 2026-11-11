"""Supplied files awaiting registration, retry, or explicit discard."""

from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Text, text
from sqlalchemy.dialects.postgresql import UUID as SQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from squid.persistence.base import Base
from squid.persistence.types import StrEnumText
from squid.submissions.domain.intake import IntakeStatus


class SubmissionSuppliedAttachment(Base, kw_only=True):
    """A supplied file retained before download and resolved only by registration or explicit discard."""

    __tablename__ = "submission_supplied_attachments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'ready', 'failed', 'discarded')", name="submission_supplied_attachments_status_check"
        ),
        CheckConstraint(
            "kind IN ('schematic', 'image', 'video', 'unknown')", name="submission_supplied_attachments_kind_check"
        ),
    )
    draft_id: Mapped[UUID] = mapped_column(
        SQLUUID(as_uuid=True), ForeignKey("submission_drafts.id", ondelete="CASCADE"), primary_key=True
    )
    source_id: Mapped[UUID] = mapped_column(SQLUUID(as_uuid=True), primary_key=True)
    filename: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    status: Mapped[IntakeStatus] = mapped_column(StrEnumText(IntakeStatus))
    discarded: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    history_managed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
