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
from squid.persistence.types import InstantUTC, now


class MediaUploadRecord(Base, kw_only=True):
    """One raw upload, described by the facts a worker re-verifies before normalizing it.

    Every column but `raw_deleted_at` is immutable once written; a retry of the same id must match.
    """

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
    """Caller-minted upload id, also the identity of the normalization job."""
    draft_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    """The submission draft this upload belongs to; limits are counted per draft."""
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    """`image` or `video`. Only a video may set `strip_audio` or produce a poster artifact."""
    source_content_type: Mapped[str] = mapped_column(Text, nullable=False)
    """Content type claimed at upload. Never trusted for typing; the worker probes the bytes."""
    source_byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    """Size of the raw object in bytes."""
    source_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    """Lowercase hex SHA-256 of the raw bytes, re-verified before normalization."""
    source_object_key: Mapped[str] = mapped_column(Text, nullable=False)
    """Key of the raw object in storage. Unique, so two uploads never share staged bytes."""
    strip_audio: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)
    """Whether the normalized video drops its audio streams."""
    raw_deleted_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    """When the raw object's deletion was confirmed; `NULL` while it is still in storage."""
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )


class MediaArtifactRecord(Base, kw_only=True):
    """One file normalization produced for an upload, at most one per role."""

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
        Index("media_artifacts_object_key_idx", "object_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    upload_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("media_uploads.id", name="media_artifacts_upload_id_fkey", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    """`output`, `poster`, or `report` — the normalized file, its still image, or the JSON report."""
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    """Key of the artifact in storage, derived from `sha256` so identical bytes share one object."""
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    """Artifact size in bytes; the sum over a draft's outputs is limit-checked at completion."""
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    """Lowercase hex SHA-256 of the artifact bytes."""
    width: Mapped[int | None] = mapped_column(Integer, default=None)
    """Pixel width, `NULL` for the `report` role and required for the others."""
    height: Mapped[int | None] = mapped_column(Integer, default=None)
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )


class MediaArtifactObjectRecord(Base, kw_only=True):
    """One artifact object key and its deletion state; the row outlives every artifact referencing it."""

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
    """Lowercase hex SHA-256 of the object. Registering the key again with a different digest fails."""
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    first_upload_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    """The upload that first registered this key."""
    last_upload_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    """The upload that registered this key most recently; identical bytes reuse one object."""
    available_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
    """When cleanup may next try this key, and the only column deletion backoff writes."""
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    """Failed deletion attempts, reset to zero when the key is re-registered after a deletion."""
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
    deleted_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    """When the object's deletion was confirmed; `NULL` means the bytes are still in storage."""
    cleanup_claimed_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    """When the live cleanup claim was taken; set together with `cleanup_claim_token` or not at all."""
    cleanup_claim_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    """Fence a cleaner's acknowledgement must still match, so a stale cleaner cannot report a delete."""
    first_seen_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
    last_seen_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
    """Last registration of this key. A move during a delete makes cleanup retry immediately."""


class MediaArtifactPublicationRecord(Base, kw_only=True):
    """One claim's lease on an object key while it writes those bytes; cleanup skips a leased key."""

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
    """The job claim that holds this lease; a reclaimed job's old token no longer protects the key."""
    expires_at: Mapped[Instant] = mapped_column(InstantUTC(), nullable=False)
    """When the lease stops protecting the key, so a lost worker's keys become collectable."""
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
    renewed_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )


class MediaNormalizationJobRecord(Base, kw_only=True):
    """One normalization request per upload, retained after it ends so clients can read its outcome."""

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
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"), default="pending")
    """`pending`, `claimed`, `completed`, `dead`, or `discarded`; the last three are terminal."""
    available_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
    """When the job next becomes claimable, and the only column retry backoff writes."""
    claimed_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    """When the live claim was taken; a claim older than the visibility timeout is reclaimable."""
    claim_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    """Fence the claiming worker's acknowledgements must still match; reclaiming mints a new one."""
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    """Failed attempts so far. The job dies once this reaches the runner's `max_attempts`."""
    completed_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    dead_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    discarded_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    """When the upload was withdrawn from its draft, which invalidates any live claim."""
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
    """The most recent failure, truncated to 4000 characters."""
