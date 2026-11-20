"""add schematic storage

Revision ID: f3a9c25b7e41
Revises: e2a48f6b91c7
Create Date: 2026-08-05 10:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f3a9c25b7e41"
down_revision: str | Sequence[str] | None = "e2a48f6b91c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MAX_SCHEMATIC_BYTES = 2 * 1024 * 1024

# Minecraft's published data versions for Java releases. This is reference data, not a
# derivation: it exists so `/build schematic convert` can name a version instead of a number.
# A release missing from this list simply has no known data version, which the resolver reports
# as "unknown" rather than guessing — a wrong number here would produce a confidently wrong
# conversion, so extending the list is preferable to interpolating it.
JAVA_DATA_VERSIONS: tuple[tuple[int, int, int, int], ...] = (
    (1, 9, 0, 169),
    (1, 9, 1, 175),
    (1, 9, 2, 176),
    (1, 9, 3, 183),
    (1, 9, 4, 184),
    (1, 10, 0, 510),
    (1, 10, 1, 511),
    (1, 10, 2, 512),
    (1, 11, 0, 819),
    (1, 11, 1, 921),
    (1, 11, 2, 922),
    (1, 12, 0, 1139),
    (1, 12, 1, 1241),
    (1, 12, 2, 1343),
    (1, 13, 0, 1519),
    (1, 13, 1, 1628),
    (1, 13, 2, 1631),
    (1, 14, 0, 1952),
    (1, 14, 1, 1957),
    (1, 14, 2, 1963),
    (1, 14, 3, 1968),
    (1, 14, 4, 1976),
    (1, 15, 0, 2225),
    (1, 15, 1, 2227),
    (1, 15, 2, 2230),
    (1, 16, 0, 2566),
    (1, 16, 1, 2567),
    (1, 16, 2, 2578),
    (1, 16, 3, 2580),
    (1, 16, 4, 2584),
    (1, 16, 5, 2586),
    (1, 17, 0, 2724),
    (1, 17, 1, 2730),
    (1, 18, 0, 2860),
    (1, 18, 1, 2865),
    (1, 18, 2, 2975),
    (1, 19, 0, 3105),
    (1, 19, 1, 3117),
    (1, 19, 2, 3120),
    (1, 19, 3, 3218),
    (1, 19, 4, 3337),
    (1, 20, 0, 3463),
    (1, 20, 1, 3465),
    (1, 20, 2, 3578),
    (1, 20, 3, 3698),
    (1, 20, 4, 3700),
    (1, 20, 5, 3837),
    (1, 20, 6, 3839),
    (1, 21, 0, 3953),
    (1, 21, 1, 3955),
    (1, 21, 2, 4080),
    (1, 21, 3, 4082),
    (1, 21, 4, 4189),
)


def upgrade() -> None:
    """Add content-addressed schematic storage and the analyses attached to builds."""
    op.create_table(
        "schematic_files",
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("source_format", sa.Text(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            f"byte_size > 0 AND byte_size <= {MAX_SCHEMATIC_BYTES}", name="schematic_files_size_bounded"
        ),
        sa.PrimaryKeyConstraint("sha256"),
        comment=(
            "Schematic bytes, content-addressed by SHA-256.\n\n"
            "Held in Postgres rather than an object host because these bytes are re-read on every\n"
            "re-render, diff, and duplicate check; the alternative is an HTTP fetch of an\n"
            "attacker-influenced URL on each one. Content addressing also means a byte-identical\n"
            "resubmission is recognised before any analysis runs."
        ),
    )

    op.create_table(
        "build_schematics",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("build_id", sa.BigInteger(), nullable=False),
        sa.Column("file_sha256", sa.Text(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("length", sa.Integer(), nullable=False),
        sa.Column("allocated_width", sa.Integer(), nullable=False),
        sa.Column("allocated_height", sa.Integer(), nullable=False),
        sa.Column("allocated_length", sa.Integer(), nullable=False),
        sa.Column("block_count", sa.Integer(), nullable=False),
        sa.Column("bounding_volume", sa.BigInteger(), nullable=False),
        sa.Column("entity_count", sa.Integer(), nullable=False),
        sa.Column("palette_size", sa.Integer(), nullable=False),
        sa.Column("region_names", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("source_data_version", sa.Integer(), nullable=True),
        sa.Column("declared_name", sa.Text(), nullable=True),
        sa.Column("declared_author", sa.Text(), nullable=True),
        sa.Column("signs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("fingerprint_structural", sa.Text(), nullable=True),
        sa.Column("fingerprint_shape", sa.Text(), nullable=True),
        sa.Column("fingerprint_exact", sa.Text(), nullable=True),
        sa.Column("signature_structural", sa.Text(), nullable=True),
        sa.Column("analyzer_version", sa.Text(), nullable=False),
        sa.Column("analysis_schema_version", sa.SmallInteger(), nullable=False),
        sa.Column("lattice", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("uploaded_by_discord_id", sa.BigInteger(), nullable=True),
        sa.Column("analyzed_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["build_id"], ["builds.id"], name="build_schematics_build_id_fkey", ondelete="CASCADE"),
        # RESTRICT, not CASCADE: several builds can reference one file, and dropping the bytes
        # would strand every analysis that describes them.
        sa.ForeignKeyConstraint(
            ["file_sha256"],
            ["schematic_files.sha256"],
            name="build_schematics_file_sha256_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("build_id", "file_sha256", name="build_schematics_build_file_key"),
        comment=(
            "One analyzed schematic attached to a build.\n\n"
            "Metrics and fingerprints are denormalised onto this row so duplicate shortlisting is a\n"
            "plain indexed query. Fingerprints are only comparable within the `analyzer_version` that\n"
            "produced them, so every identity index carries that column and every lookup filters on it;\n"
            "an engine upgrade therefore becomes a visible backfill rather than a silent regression."
        ),
    )
    op.create_index("build_schematics_block_count_idx", "build_schematics", ["block_count"])
    op.create_index("build_schematics_build_id_idx", "build_schematics", ["build_id"])
    # Fingerprints are translation-invariant hashes, so equality is the only predicate SQL can
    # answer about them; the analyzer version rides along because a hash from another engine
    # build is not comparable.
    op.create_index(
        "build_schematics_fingerprint_shape_idx",
        "build_schematics",
        ["fingerprint_shape", "analyzer_version"],
        postgresql_where=sa.text("fingerprint_shape IS NOT NULL"),
    )
    op.create_index(
        "build_schematics_fingerprint_structural_idx",
        "build_schematics",
        ["fingerprint_structural", "analyzer_version"],
        postgresql_where=sa.text("fingerprint_structural IS NOT NULL"),
    )
    op.create_index(
        "build_schematics_one_primary_per_build",
        "build_schematics",
        ["build_id"],
        unique=True,
        postgresql_where=sa.text("is_primary"),
    )

    op.add_column("versions", sa.Column("data_version", sa.Integer(), nullable=True))
    _seed_java_data_versions()


def _seed_java_data_versions() -> None:
    """Fill in data versions for whichever Java releases this database already knows.

    An UPDATE rather than an INSERT: the version catalogue is populated at runtime by the
    version-tracking task, and this migration's job is only to annotate what is there. Rows the
    catalogue gains later are annotated by re-running this statement, not by this revision.
    """
    values = ", ".join(
        f"({major}, {minor}, {patch}, {data_version})" for major, minor, patch, data_version in JAVA_DATA_VERSIONS
    )
    op.execute(
        f"""
        UPDATE public.versions v
        SET data_version = s.data_version
        FROM (VALUES {values}) AS s(major_version, minor_version, patch_number, data_version)
        WHERE v.edition = 'Java'
          AND v.major_version = s.major_version
          AND v.minor_version = s.minor_version
          AND v.patch_number = s.patch_number
        """
    )


def downgrade() -> None:
    """Drop schematic storage. The stored bytes are lost; they are re-uploadable."""
    op.drop_column("versions", "data_version")
    op.drop_index("build_schematics_one_primary_per_build", table_name="build_schematics")
    op.drop_index("build_schematics_fingerprint_structural_idx", table_name="build_schematics")
    op.drop_index("build_schematics_fingerprint_shape_idx", table_name="build_schematics")
    op.drop_index("build_schematics_build_id_idx", table_name="build_schematics")
    op.drop_index("build_schematics_block_count_idx", table_name="build_schematics")
    op.drop_table("build_schematics")
    op.drop_table("schematic_files")
