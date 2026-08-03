"""SQLAlchemy schematic storage models."""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from whenever import Instant

from squid.persistence.base import Base
from squid.persistence.types import InstantUTC

MAX_SCHEMATIC_BYTES = 2 * 1024 * 1024
"""Hard ceiling enforced in the database as well as at upload. Real doors are single-digit
kilobytes, so this is generous by three orders of magnitude."""


class SchematicFile(Base):
    """Schematic bytes, content-addressed by SHA-256.

    Held in Postgres rather than an object host because these bytes are re-read on every
    re-render, diff, and duplicate check; the alternative is an HTTP fetch of an
    attacker-influenced URL on each one. Content addressing also means a byte-identical
    resubmission is recognised before any analysis runs.
    """

    __tablename__ = "schematic_files"
    __table_args__ = (
        CheckConstraint(f"byte_size > 0 AND byte_size <= {MAX_SCHEMATIC_BYTES}", name="schematic_files_size_bounded"),
    )

    sha256: Mapped[str] = mapped_column(Text, primary_key=True)
    """Lowercase hex SHA-256 of `data`, and the identity of this row."""
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    """The uploaded file exactly as received, uncompressed and unmodified."""
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    """Size of `data`. Stored so size predicates never have to detoast the bytes."""
    source_format: Mapped[str] = mapped_column(Text, nullable=False)
    """The format the content sniffer identified, e.g. `litematic`."""
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )


class BuildSchematic(Base, kw_only=True):
    """One analyzed schematic attached to a build.

    Metrics and fingerprints are denormalised onto this row so duplicate shortlisting is a
    plain indexed query. Fingerprints are only comparable within the `analyzer_version` that
    produced them, so every identity index carries that column and every lookup filters on it;
    an engine upgrade therefore becomes a visible backfill rather than a silent regression.
    """

    __tablename__ = "build_schematics"
    __table_args__ = (
        UniqueConstraint("build_id", "file_sha256", name="build_schematics_build_file_key"),
        # At most one primary schematic per build, while still allowing many secondary ones.
        Index(
            "build_schematics_one_primary_per_build",
            "build_id",
            unique=True,
            postgresql_where=text("is_primary"),
        ),
        Index("build_schematics_build_id_idx", "build_id"),
        # Fingerprints are translation-invariant hashes: equality is the only predicate SQL
        # can answer about them, and near-duplicate ranking happens pairwise in the worker
        # over the shortlist these indexes produce.
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
    """The stored bytes this analysis describes. `RESTRICT` because several builds can share
    one file and losing it would strand every analysis that references it."""
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    """Whether this is the schematic shown on the build card and used for duplicate checks."""
    original_filename: Mapped[str | None] = mapped_column(Text, default=None)
    """The name the uploader's file had, kept for display only. Never trusted for typing."""

    width: Mapped[int] = mapped_column(Integer, nullable=False)
    """Tight content width. Machine-read, unlike the human-declared value on `builds`."""
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    length: Mapped[int] = mapped_column(Integer, nullable=False)
    allocated_width: Mapped[int] = mapped_column(Integer, nullable=False)
    """Width of the region the file allocates, which can far exceed the tight content."""
    allocated_height: Mapped[int] = mapped_column(Integer, nullable=False)
    allocated_length: Mapped[int] = mapped_column(Integer, nullable=False)
    block_count: Mapped[int] = mapped_column(Integer, nullable=False)
    """Non-air block count. **Not** the Door Rules cumulative volume, which counts air pockets
    and carries hallway, frame, and hitbox exceptions no static read can apply."""
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
    """Coarse translation-invariant bucket. A build differing by a single block still matches,
    so this is a pre-filter feeding pairwise ranking and never a duplicate verdict by itself."""
    fingerprint_shape: Mapped[str | None] = mapped_column(Text, default=None)
    """Translation- and rotation-invariant identity. This is the primary duplicate index."""
    fingerprint_exact: Mapped[str | None] = mapped_column(Text, default=None)
    """Material- and orientation-sensitive identity, the strict tier."""
    signature_structural: Mapped[str | None] = mapped_column(Text, default=None)
    """The engine's structural signature document, kept for pre-filter experiments."""
    analyzer_version: Mapped[str] = mapped_column(Text, nullable=False)
    """Which engine build produced the fingerprints on this row, e.g. `nucleation-0.9.2`."""
    analysis_schema_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    """Which revision of *our* analysis produced this row, bumped when we change what we read."""

    lattice: Mapped[dict[str, object] | None] = mapped_column(JSONB, default=None)
    """The highest-coverage repeating unit cell found, if the build has one."""

    uploaded_by_discord_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    analyzed_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )
