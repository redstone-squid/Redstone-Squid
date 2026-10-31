"""SQLAlchemy models for synchronized submission drafts."""

import uuid

from sqlalchemy import (
    BigInteger,
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
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from whenever import Instant

from squid.persistence.base import Base
from squid.persistence.types import InstantUTC, StrEnumText, now
from squid.submissions.domain import DraftStatus, SubmissionOrigin


class SubmissionDraft(Base, kw_only=True):
    """The compact current state of an account-owned submission draft."""

    __tablename__ = "submission_drafts"
    __table_args__ = (
        CheckConstraint("schema_revision > 0", name="submission_drafts_schema_revision_positive"),
        CheckConstraint("revision >= 0", name="submission_drafts_revision_nonnegative"),
        CheckConstraint(
            "origin IN ('discord', 'web', 'cli', 'paper', 'fabric')",
            name="submission_drafts_origin_check",
        ),
        CheckConstraint(
            "status IN ('editing', 'processing', 'needs_attention', 'submitted', 'expired')",
            name="submission_drafts_status_check",
        ),
        CheckConstraint(
            "source_installation_id IS NULL OR origin = 'paper'",
            name="submission_drafts_installation_requires_paper",
        ),
        CheckConstraint("expires_at > created_at", name="submission_drafts_expiry_after_creation"),
        Index("submission_drafts_owner_updated_idx", "owner_account_id", "updated_at"),
        Index(
            "submission_drafts_expiry_idx",
            "expires_at",
            postgresql_where=text("status IN ('editing', 'processing', 'needs_attention')"),
        ),
        Index(
            "submission_drafts_source_installation_idx",
            "source_installation_id",
            postgresql_where=text("source_installation_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default_factory=uuid.uuid4)
    owner_account_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("accounts.id", name="submission_drafts_owner_account_id_fkey", ondelete="CASCADE"),
        nullable=False,
    )
    schema_id: Mapped[str] = mapped_column(Text, nullable=False)
    schema_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    status: Mapped[DraftStatus] = mapped_column(
        StrEnumText(DraftStatus),
        nullable=False,
        server_default=text("'editing'"),
        default=DraftStatus.EDITING,
    )
    answers: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default_factory=dict)
    origin: Mapped[SubmissionOrigin] = mapped_column(StrEnumText(SubmissionOrigin), nullable=False)
    source_installation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        default=None,
    )
    """Server-derived Paper installation retained independently of credential generations."""
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
    updated_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
    expires_at: Mapped[Instant] = mapped_column(InstantUTC(), nullable=False)


class SubmissionDraftAccess(Base, kw_only=True):
    """An account's role on a draft; v1 creates exactly one owner grant."""

    __tablename__ = "submission_draft_access"
    __table_args__ = (
        UniqueConstraint("draft_id", "account_id", name="submission_draft_access_draft_account_key"),
        CheckConstraint("role IN ('owner', 'editor')", name="submission_draft_access_role_check"),
        Index(
            "submission_draft_access_one_owner",
            "draft_id",
            unique=True,
            postgresql_where=text("role = 'owner'"),
        ),
        Index("submission_draft_access_account_idx", "account_id", "draft_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    draft_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("submission_drafts.id", name="submission_draft_access_draft_id_fkey", ondelete="CASCADE"),
        nullable=False,
    )
    account_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("accounts.id", name="submission_draft_access_account_id_fkey", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )


class SubmissionDraftChange(Base, kw_only=True):
    """An immutable accepted field-operation batch used for retries and audit."""

    __tablename__ = "submission_draft_changes"
    __table_args__ = (
        UniqueConstraint("draft_id", "resulting_revision", name="submission_draft_changes_draft_revision_key"),
        UniqueConstraint("draft_id", "idempotency_key", name="submission_draft_changes_draft_idempotency_key"),
        CheckConstraint("base_revision >= 0", name="submission_draft_changes_base_revision_nonnegative"),
        CheckConstraint(
            "resulting_revision = base_revision + 1",
            name="submission_draft_changes_revision_sequence",
        ),
        CheckConstraint("jsonb_array_length(operations) > 0", name="submission_draft_changes_has_operations"),
        Index("submission_draft_changes_actor_idx", "actor_account_id", "applied_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    draft_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("submission_drafts.id", name="submission_draft_changes_draft_id_fkey", ondelete="CASCADE"),
        nullable=False,
    )
    actor_account_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("accounts.id", name="submission_draft_changes_actor_account_id_fkey", ondelete="RESTRICT"),
        nullable=False,
    )
    base_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    resulting_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    client_instance_id: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    operations: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    applied_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
