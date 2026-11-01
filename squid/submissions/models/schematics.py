"""Private schematic sources and their independently editable draft references."""

from uuid import UUID

from sqlalchemy import BigInteger, Boolean, CheckConstraint, ForeignKey, Index, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import UUID as SQLUUID
from sqlalchemy.orm import Mapped, mapped_column
from whenever import Instant

from squid.persistence.base import Base
from squid.persistence.types import InstantUTC, StrEnumText, now
from squid.schematics.domain.models import SCHEMATIC_FILE_SCHEMA_MAX_BYTES
from squid.submissions.domain.schematics import DraftSchematicState


class DraftSchematicSource(Base, kw_only=True):
    """Private source bytes tracked before upload and retained until reference-safe deletion."""

    __tablename__ = "submission_schematic_sources"
    __table_args__ = (
        CheckConstraint(
            f"byte_size IS NULL OR (byte_size > 0 AND byte_size <= {SCHEMATIC_FILE_SCHEMA_MAX_BYTES})",
            name="submission_schematic_sources_size_check",
        ),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="submission_schematic_sources_sha_check"),
        CheckConstraint(
            "state IN ('uploading', 'waiting_sanitizer', 'failed', 'deleting', 'deleted')",
            name="submission_schematic_sources_state_check",
        ),
        CheckConstraint(
            "state <> 'waiting_sanitizer' OR (sha256 IS NOT NULL AND byte_size IS NOT NULL)",
            name="submission_schematic_sources_uploaded_check",
        ),
        Index("submission_schematic_sources_owner_idx", "owner_account_id"),
        Index("submission_schematic_sources_actor_idx", "uploaded_by_account_id"),
        Index("submission_schematic_sources_cleanup_idx", "updated_at", postgresql_where=text("state <> 'deleted'")),
    )

    id: Mapped[UUID] = mapped_column(SQLUUID(as_uuid=True), primary_key=True)
    owner_account_id: Mapped[int] = mapped_column(Integer, ForeignKey("accounts.id", ondelete="RESTRICT"))
    uploaded_by_account_id: Mapped[int] = mapped_column(Integer, ForeignKey("accounts.id", ondelete="RESTRICT"))
    filename: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str | None] = mapped_column(Text)
    byte_size: Mapped[int | None] = mapped_column(BigInteger)
    state: Mapped[DraftSchematicState] = mapped_column(StrEnumText(DraftSchematicState))
    updated_at: Mapped[Instant] = mapped_column(InstantUTC(), default_factory=now, server_default=func.now())
    created_at: Mapped[Instant] = mapped_column(InstantUTC(), default_factory=now, server_default=func.now())


class DraftSchematicLink(Base, kw_only=True):
    """One draft's retained schematic reference and explicit primary selection."""

    __tablename__ = "submission_draft_schematics"
    __table_args__ = (
        CheckConstraint("NOT discarded OR NOT is_primary", name="submission_draft_schematics_discarded_primary_check"),
        Index("submission_draft_schematics_source_idx", "source_id"),
        Index("submission_draft_schematics_one_primary", "draft_id", unique=True, postgresql_where=text("is_primary")),
    )

    draft_id: Mapped[UUID] = mapped_column(
        SQLUUID(as_uuid=True), ForeignKey("submission_drafts.id", ondelete="CASCADE"), primary_key=True
    )
    source_id: Mapped[UUID] = mapped_column(
        SQLUUID(as_uuid=True), ForeignKey("submission_schematic_sources.id", ondelete="RESTRICT"), primary_key=True
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    discarded: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
