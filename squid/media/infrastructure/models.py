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

from squid.media.application.jobs import MediaArtifactRole, MediaJobStatus
from squid.media.domain import MediaKind
from squid.persistence.base import Base
from squid.persistence.types import InstantUTC, StrEnumText, now


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
    kind: Mapped[MediaKind] = mapped_column(StrEnumText(MediaKind), nullable=False)
    source_content_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    source_object_key: Mapped[str] = mapped_column(Text, nullable=False)
    strip_audio: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)
    raw_deleted_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )


class MediaArtifactRecord(Base, kw_only=True):
    """Content-addressed normalized output, video thumbnail, or report metadata."""

    __tablename__ = "media_artifacts"
    __table_args__ = (
        UniqueConstraint("upload_id", "role", name="media_artifacts_upload_role_key"),
        CheckConstraint(
            "role IN ('output', 'poster', 'video_thumbnail', 'report')",
            name="media_artifacts_role_check",
        ),
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
        Index("media_artifacts_object_key_idx", "object_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    upload_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("media_uploads.id", name="media_artifacts_upload_id_fkey", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[MediaArtifactRole] = mapped_column(StrEnumText(MediaArtifactRole), nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer, default=None)
    height: Mapped[int | None] = mapped_column(Integer, default=None)
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )


class MediaArtifactObjectRecord(Base, kw_only=True):
    """Durable lifecycle and cleanup audit state for one content-addressed object."""

    __tablename__ = "media_artifact_objects"
    __table_args__ = (
        CheckConstraint("attempts >= 0", name="media_artifact_objects_attempts_nonnegative"),
        CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$'",
            name="media_artifact_objects_sha256_check",
        ),
        CheckConstraint("byte_size > 0", name="media_artifact_objects_size_positive"),
        CheckConstraint(
            "(cleanup_claimed_at IS NULL) = (cleanup_claim_token IS NULL)",
            name="media_artifact_objects_cleanup_claim_shape",
        ),
        Index(
            "media_artifact_objects_cleanup_idx",
            "available_at",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    object_key: Mapped[str] = mapped_column(Text, primary_key=True)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    first_upload_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    last_upload_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    available_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
    deleted_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    cleanup_claimed_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    cleanup_claim_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    first_seen_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
    last_seen_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )


class MediaArtifactPublicationRecord(Base, kw_only=True):
    """A crash-recoverable lease protecting one claim's in-flight object publication."""

    __tablename__ = "media_artifact_publications"
    __table_args__ = (
        CheckConstraint(
            "expires_at > created_at",
            name="media_artifact_publications_expiry_after_creation",
        ),
        Index("media_artifact_publications_expiry_idx", "expires_at"),
    )

    object_key: Mapped[str] = mapped_column(
        Text,
        ForeignKey(
            "media_artifact_objects.object_key",
            name="media_artifact_publications_object_key_fkey",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )
    upload_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    claim_token: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    expires_at: Mapped[Instant] = mapped_column(InstantUTC(), nullable=False)
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
    renewed_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )


class MediaNormalizationJobRecord(Base, kw_only=True):
    """A retained, claim-token-fenced request to normalize one raw upload."""

    __tablename__ = "media_normalization_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'claimed', 'completed', 'dead', 'discarded')",
            name="media_normalization_jobs_status_check",
        ),
        CheckConstraint("attempts >= 0", name="media_normalization_jobs_attempts_nonnegative"),
        CheckConstraint(
            "(status = 'pending' AND claimed_at IS NULL AND claim_token IS NULL "
            "AND completed_at IS NULL AND dead_at IS NULL AND discarded_at IS NULL) OR "
            "(status = 'claimed' AND claimed_at IS NOT NULL AND claim_token IS NOT NULL "
            "AND completed_at IS NULL AND dead_at IS NULL AND discarded_at IS NULL) OR "
            "(status = 'completed' AND claimed_at IS NULL AND claim_token IS NULL "
            "AND completed_at IS NOT NULL AND dead_at IS NULL AND discarded_at IS NULL) OR "
            "(status = 'dead' AND claimed_at IS NULL AND claim_token IS NULL "
            "AND completed_at IS NULL AND dead_at IS NOT NULL AND discarded_at IS NULL) OR "
            "(status = 'discarded' AND claimed_at IS NULL AND claim_token IS NULL "
            "AND completed_at IS NULL AND dead_at IS NULL AND discarded_at IS NOT NULL)",
            name="media_normalization_jobs_state_shape",
        ),
        Index(
            "media_normalization_jobs_ready_idx",
            "available_at",
            postgresql_where=text("status IN ('pending', 'claimed')"),
        ),
        Index(
            "media_normalization_jobs_terminal_idx",
            "upload_id",
            postgresql_where=text("status IN ('completed', 'dead', 'discarded')"),
        ),
    )

    upload_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("media_uploads.id", name="media_normalization_jobs_upload_id_fkey", ondelete="CASCADE"),
        primary_key=True,
    )
    status: Mapped[MediaJobStatus] = mapped_column(
        StrEnumText(MediaJobStatus),
        nullable=False,
        server_default=text("'pending'"),
        default=MediaJobStatus.PENDING,
    )
    available_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
    claimed_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    claim_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    completed_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    dead_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    discarded_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
