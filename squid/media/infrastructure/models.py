"""SQLAlchemy models for durable media normalization."""

import uuid

from sqlalchemy import (
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
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from whenever import Instant

from squid.persistence.base import Base
from squid.persistence.types import InstantUTC


class MediaUploadRecord(Base, kw_only=True):
    """Immutable raw-upload metadata used to verify worker input."""

    __tablename__ = "media_uploads"
    __table_args__ = (
        CheckConstraint("kind IN ('image', 'video')", name="media_uploads_kind_check"),
        CheckConstraint("source_byte_size > 0", name="media_uploads_source_size_positive"),
        CheckConstraint(
            "source_sha256 ~ '^[0-9a-f]{64}$'",
            name="media_uploads_source_sha256_check",
        ),
        CheckConstraint(
            "char_length(source_content_type) BETWEEN 1 AND 255",
            name="media_uploads_content_type_length",
        ),
        CheckConstraint(
            "kind = 'video' OR strip_audio = false",
            name="media_uploads_audio_only_for_video",
        ),
        UniqueConstraint("source_object_key", name="media_uploads_source_object_key_key"),
        Index("media_uploads_draft_idx", "draft_id", "created_at"),
        Index(
            "media_uploads_raw_cleanup_idx",
            "created_at",
            postgresql_where=text("raw_deleted_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    draft_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    source_content_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    source_object_key: Mapped[str] = mapped_column(Text, nullable=False)
    strip_audio: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)
    raw_deleted_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )


class MediaArtifactRecord(Base, kw_only=True):
    """Content-addressed normalized output, poster, or disclosure report metadata."""

    __tablename__ = "media_artifacts"
    __table_args__ = (
        UniqueConstraint("upload_id", "role", name="media_artifacts_upload_role_key"),
        CheckConstraint("role IN ('output', 'poster', 'report')", name="media_artifacts_role_check"),
        CheckConstraint("byte_size > 0", name="media_artifacts_size_positive"),
        CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$'",
            name="media_artifacts_sha256_check",
        ),
        CheckConstraint(
            "(role = 'report' AND width IS NULL AND height IS NULL) OR (role <> 'report' AND width > 0 AND height > 0)",
            name="media_artifacts_dimensions_by_role",
        ),
        Index("media_artifacts_sha256_idx", "sha256"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    upload_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("media_uploads.id", name="media_artifacts_upload_id_fkey", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer, default=None)
    height: Mapped[int | None] = mapped_column(Integer, default=None)
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )


class MediaNormalizationJobRecord(Base, kw_only=True):
    """A retained, claim-token-fenced request to normalize one raw upload."""

    __tablename__ = "media_normalization_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'claimed', 'completed', 'dead')",
            name="media_normalization_jobs_status_check",
        ),
        CheckConstraint("attempts >= 0", name="media_normalization_jobs_attempts_nonnegative"),
        CheckConstraint(
            "(status = 'pending' AND claimed_at IS NULL AND claim_token IS NULL "
            "AND completed_at IS NULL AND dead_at IS NULL) OR "
            "(status = 'claimed' AND claimed_at IS NOT NULL AND claim_token IS NOT NULL "
            "AND completed_at IS NULL AND dead_at IS NULL) OR "
            "(status = 'completed' AND claimed_at IS NULL AND claim_token IS NULL "
            "AND completed_at IS NOT NULL AND dead_at IS NULL) OR "
            "(status = 'dead' AND claimed_at IS NULL AND claim_token IS NULL "
            "AND completed_at IS NULL AND dead_at IS NOT NULL)",
            name="media_normalization_jobs_state_shape",
        ),
        Index(
            "media_normalization_jobs_ready_idx",
            "available_at",
            postgresql_where=text("status IN ('pending', 'claimed')"),
        ),
        Index(
            "media_normalization_jobs_terminal_idx",
            "completed_at",
            postgresql_where=text("status IN ('completed', 'dead')"),
        ),
    )

    upload_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("media_uploads.id", name="media_normalization_jobs_upload_id_fkey", ondelete="CASCADE"),
        primary_key=True,
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"), default="pending")
    available_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )
    claimed_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    claim_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    completed_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    dead_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
