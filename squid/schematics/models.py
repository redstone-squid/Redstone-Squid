"""SQLAlchemy schematic storage models."""

import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from whenever import Instant

from squid.persistence.base import Base
from squid.persistence.types import InstantUTC, now
from squid.schematics.domain.models import SCHEMATIC_FILE_SCHEMA_MAX_BYTES


class SchematicFile(Base):
    """Relational metadata for a content-addressed schematic artifact."""

    __tablename__ = "schematic_files"
    __table_args__ = (
        CheckConstraint(
            f"byte_size > 0 AND byte_size <= {SCHEMATIC_FILE_SCHEMA_MAX_BYTES}",
            name="schematic_files_size_bounded",
        ),
    )

    sha256: Mapped[str] = mapped_column(Text, primary_key=True)
    """Lowercase hex SHA-256 of the object payload, and the identity of this row."""
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    """Object payload size used to bound downloads."""
    source_format: Mapped[str] = mapped_column(Text, nullable=False)
    """The format the content sniffer identified, e.g. `litematic`."""
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )


class BuildSchematic(Base, kw_only=True):
    """One analyzed file attached to a build, with its publication policy.

    Measurements and fingerprints are stored here for duplicate shortlisting. Fingerprint lookups
    include analyzer_version because an engine upgrade can change the hashes. Reanalyzing an
    attachment updates this evidence without changing its source bytes.
    """

    __tablename__ = "build_schematics"
    __table_args__ = (
        Index("build_schematics_uploaded_by_idx", "uploaded_by_account_id"),
        Index("build_schematics_rights_attested_by_idx", "rights_attested_by_account_id"),
        UniqueConstraint("build_id", "file_sha256", name="build_schematics_build_file_key"),
        # At most one primary schematic per build, while still allowing many secondary ones.
        Index(
            "build_schematics_one_primary_per_build",
            "build_id",
            unique=True,
            postgresql_where=text("is_primary"),
        ),
        Index("build_schematics_build_id_idx", "build_id"),
        Index("build_schematics_file_sha256_idx", "file_sha256"),
        # Hash equality provides a shortlist; the worker compares files to rank near duplicates.
        Index(
            "build_schematics_fingerprint_structural_idx",
            "fingerprint_structural",
            "analyzer_version",
            postgresql_where=text("fingerprint_structural IS NOT NULL"),
        ),
        Index(
            "build_schematics_fingerprint_shape_idx",
            "fingerprint_shape",
            "analyzer_version",
            postgresql_where=text("fingerprint_shape IS NOT NULL"),
        ),
        Index("build_schematics_block_count_idx", "block_count"),
        CheckConstraint(
            "visibility IN ('legacy_unverified', 'reviewer_only', 'public_download')",
            name="build_schematics_visibility_check",
        ),
        CheckConstraint(
            "visibility <> 'public_download' OR (license_code IS NOT NULL AND rights_attested_at IS NOT NULL "
            "AND rights_attested_by_account_id IS NOT NULL)",
            name="build_schematics_publication_complete",
        ),
        CheckConstraint(
            "license_code IS NULL OR license_code IN ("
            "'cc0_1_0', 'cc_by_4_0', 'cc_by_sa_4_0', 'cc_by_nd_4_0', "
            "'cc_by_nc_4_0', 'cc_by_nc_sa_4_0', 'cc_by_nc_nd_4_0')",
            name="build_schematics_license_check",
        ),
        Index(
            "build_schematics_public_download_idx",
            "build_id",
            "id",
            postgresql_where=text("visibility = 'public_download' AND withdrawn_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, init=False)
    build_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("builds.id", name="build_schematics_build_id_fkey", ondelete="CASCADE"),
        nullable=False,
    )
    file_sha256: Mapped[str] = mapped_column(
        Text,
        ForeignKey("schematic_files.sha256", name="build_schematics_file_sha256_fkey", ondelete="RESTRICT"),
        nullable=False,
    )
    """Digest of the analyzed bytes, which may be shared by several builds.

    Deletion is restricted while any attachment references the file.
    """
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    """Featured attachment used for generated previews, conversion, and timing measurements."""
    original_filename: Mapped[str | None] = mapped_column(Text, default=None)
    """Original display filename; ingestion may also use its extension as a fallback format hint."""

    width: Mapped[int] = mapped_column(Integer, nullable=False)
    """Tight content width. Machine-read, unlike the human-declared value on `builds`."""
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    length: Mapped[int] = mapped_column(Integer, nullable=False)
    allocated_width: Mapped[int] = mapped_column(Integer, nullable=False)
    """Width of the region the file allocates, which can far exceed the tight content."""
    allocated_height: Mapped[int] = mapped_column(Integer, nullable=False)
    allocated_length: Mapped[int] = mapped_column(Integer, nullable=False)
    block_count: Mapped[int] = mapped_column(Integer, nullable=False)
    """Non-air block count, separate from the Door Rules cumulative volume.

    The latter includes air pockets and needs hallway, frame, and hitbox review.
    """
    bounding_volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    """Tight bounding box volume including air. Materialised so it can be range-scanned."""
    entity_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    palette_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    """How many distinct block states the file declares."""
    region_names: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default_factory=list)
    source_data_version: Mapped[int | None] = mapped_column(Integer, default=None)
    """The Minecraft data version the file declares, or `None` when it declares none."""
    declared_name: Mapped[str | None] = mapped_column(Text, default=None)
    declared_author: Mapped[str | None] = mapped_column(Text, default=None)
    signs: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False, default_factory=list)
    """Sign text recovered from the schematic, as `{x, y, z, text}` objects."""

    fingerprint_structural: Mapped[str | None] = mapped_column(Text, default=None)
    """Coarse translation-invariant hash used to shortlist files for pairwise comparison.

    Matching values can include different block arrangements and do not establish duplication.
    """
    fingerprint_shape: Mapped[str | None] = mapped_column(Text, default=None)
    """Translation- and rotation-invariant identity. This is the primary duplicate index."""
    fingerprint_exact: Mapped[str | None] = mapped_column(Text, default=None)
    """Material- and orientation-sensitive identity, the strict tier."""
    signature_structural: Mapped[str | None] = mapped_column(Text, default=None)
    """The engine's structural signature document, kept for pre-filter experiments."""
    analyzer_version: Mapped[str] = mapped_column(Text, nullable=False)
    """Engine version that produced these fingerprints; comparisons must use the same version."""
    analysis_schema_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    """Application analysis format version, independent of the engine version."""

    lattice: Mapped[dict[str, object] | None] = mapped_column(JSONB(none_as_null=True), default=None)
    """The highest-coverage repeating unit cell found, if the build has one."""
    simulation_evidence: Mapped[dict[str, object] | None] = mapped_column(JSONB(none_as_null=True), default=None)
    """Staff-triggered tick-engine evidence. It never changes the build's declared timing."""

    visibility: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'legacy_unverified'"),
        default="legacy_unverified",
    )
    """Explicit download choice. Existing attachments remain private until re-attested."""
    license_code: Mapped[str | None] = mapped_column(Text, default=None)
    rights_attested_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    rights_attested_by_account_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "accounts.id",
            name="build_schematics_rights_attested_by_account_id_fkey",
            ondelete="RESTRICT",
        ),
        default=None,
    )
    published_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    withdrawn_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)

    uploaded_by_account_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "accounts.id",
            name="build_schematics_uploaded_by_account_id_fkey",
            ondelete="SET NULL",
        ),
        default=None,
    )
    """Account that supplied the file, which can differ from the account attesting publication rights."""
    analyzed_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )


class SchematicPreviewObject(Base, kw_only=True):
    """Durable upload and cleanup state for one generated preview object."""

    __tablename__ = "schematic_preview_objects"
    __table_args__ = (
        CheckConstraint("byte_size > 0", name="schematic_preview_objects_size_positive"),
        CheckConstraint(
            "sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'",
            name="schematic_preview_objects_sha256_check",
        ),
        CheckConstraint(
            "(cleanup_claimed_at IS NULL) = (cleanup_claim_token IS NULL)",
            name="schematic_preview_objects_cleanup_claim_pair",
        ),
        Index("schematic_preview_objects_cleanup_idx", "last_seen_at"),
    )

    object_key: Mapped[str] = mapped_column(Text, primary_key=True)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str | None] = mapped_column(Text, default=None)
    ready_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    cleanup_claimed_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    """When cleanup reserved this unreferenced object for deletion."""
    cleanup_claim_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    """Token required to finish cleanup; its presence prevents reuse or publication."""
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
    last_seen_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )


class SchematicRender(Base, kw_only=True):
    """A replaceable preview artifact keyed by the complete rendering recipe."""

    __tablename__ = "schematic_renders"
    __table_args__ = (
        UniqueConstraint("build_schematic_id", "recipe_hash", name="schematic_renders_schematic_recipe_key"),
        CheckConstraint("width > 0 AND height > 0 AND byte_size > 0", name="schematic_renders_sizes_positive"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, init=False)
    build_schematic_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("build_schematics.id", name="schematic_renders_build_schematic_id_fkey", ondelete="CASCADE"),
        nullable=False,
    )
    recipe_hash: Mapped[str] = mapped_column(Text, nullable=False)
    """Hash of the source and pack digests, framing, schematic dimensions, and analyzer version."""
    url: Mapped[str] = mapped_column(Text, nullable=False)
    object_key: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey(
            "schematic_preview_objects.object_key",
            name="schematic_renders_object_key_fkey",
            ondelete="RESTRICT",
        ),
        default=None,
    )
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )


class SchematicRenderQueueItem(Base, kw_only=True):
    """A durable request to render and publish one build's primary schematic."""

    __tablename__ = "schematic_render_queue"
    __table_args__ = (
        Index(
            "schematic_render_queue_ready_idx",
            "available_at",
            postgresql_where=text("claimed_at IS NULL AND dead_at IS NULL"),
        ),
    )

    build_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("builds.id", name="schematic_render_queue_build_id_fkey", ondelete="CASCADE"),
        primary_key=True,
    )
    enqueued_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
    available_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
    """Earliest time a worker may claim this job, including retry backoff."""
    claimed_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    claim_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    """Token that must still match when the claiming worker records its outcome."""
    dead_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)


class SchematicJob(Base, kw_only=True):
    """A durable native-engine request whose result remains available until retention expires.

    Claim tokens reject stale worker updates. Binary output objects are retained alongside the row
    for cleanup; input objects can be shared by multiple jobs.
    """

    __tablename__ = "schematic_jobs"
    __table_args__ = (
        CheckConstraint(
            "operation IN ('capabilities', 'analyze', 'convert', 'compare', 'render', 'simulate', 'autostack')",
            name="schematic_jobs_operation_check",
        ),
        CheckConstraint(
            "completed_at IS NULL OR dead_at IS NULL",
            name="schematic_jobs_single_terminal_state",
        ),
        Index(
            "schematic_jobs_ready_idx",
            "available_at",
            postgresql_where=text("completed_at IS NULL AND dead_at IS NULL"),
        ),
        Index("schematic_jobs_expiry_idx", "expires_at", postgresql_where=text("expires_at IS NOT NULL")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, init=False)
    operation: Mapped[str] = mapped_column(Text, nullable=False)
    params: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default_factory=dict)
    input_keys: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default_factory=list)
    result: Mapped[dict[str, object] | None] = mapped_column(JSONB, default=None)
    result_object_key: Mapped[str | None] = mapped_column(Text, default=None)
    result_object_keys: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        default_factory=list,
        server_default=text("'{}'::text[]"),
        comment="Result objects registered by worker claims, retained until job expiry.",
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    available_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
    claimed_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    claim_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    """Token that must still match when the claiming worker records its outcome."""
    completed_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    dead_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    expires_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
    error_kind: Mapped[str | None] = mapped_column(Text, default=None)
    error_context: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default_factory=dict)
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
