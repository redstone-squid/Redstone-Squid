"""Translation between schematic domain values and their persisted rows."""

from collections.abc import Mapping, Sequence
from typing import Any, Literal, cast

from squid.schematics.application.queries import StoredSchematic
from squid.schematics.domain.models import (
    AutostackLattice,
    SchematicAnalysis,
    SchematicDimensions,
    SchematicFingerprints,
    SchematicFormat,
    SchematicMetrics,
    SchematicSign,
    Vector3,
)
from squid.schematics.infrastructure.models import BuildSchematic


def to_row_values(analysis: SchematicAnalysis) -> dict[str, Any]:
    """Flatten an analysis into the denormalised columns of `build_schematics`."""
    metrics = analysis.metrics
    fingerprints = analysis.fingerprints
    return {
        "width": metrics.dimensions.width,
        "height": metrics.dimensions.height,
        "length": metrics.dimensions.length,
        "allocated_width": metrics.allocated_dimensions.width,
        "allocated_height": metrics.allocated_dimensions.height,
        "allocated_length": metrics.allocated_dimensions.length,
        "block_count": metrics.block_count,
        "bounding_volume": metrics.bounding_volume,
        "entity_count": metrics.entity_count,
        "palette_size": metrics.palette_size,
        "region_names": list(metrics.region_names),
        "source_data_version": metrics.source_data_version,
        "declared_name": metrics.declared_name,
        "declared_author": metrics.declared_author,
        "signs": [{"x": sign.x, "y": sign.y, "z": sign.z, "text": sign.text} for sign in metrics.signs],
        "fingerprint_structural": fingerprints.structural,
        "fingerprint_shape": fingerprints.shape,
        "fingerprint_exact": fingerprints.exact,
        "signature_structural": fingerprints.signature_structural,
        "analyzer_version": analysis.analyzer_version,
        "analysis_schema_version": analysis.analysis_schema_version,
        "lattice": _lattice_to_json(analysis.lattice),
    }


def to_stored_schematic(row: BuildSchematic, *, source_format: SchematicFormat, byte_size: int) -> StoredSchematic:
    """Rebuild the read model, taking the two file-level facts from `schematic_files`.

    `source_format` and `byte_size` describe the bytes, not the analysis, so they live on the
    content-addressed file row and are joined in rather than duplicated per attachment.
    """
    return StoredSchematic(
        id=row.id,
        build_id=row.build_id,
        file_sha256=row.file_sha256,
        is_primary=row.is_primary,
        original_filename=row.original_filename,
        analysis=SchematicAnalysis(
            metrics=SchematicMetrics(
                source_format=source_format,
                byte_size=byte_size,
                sha256=row.file_sha256,
                dimensions=SchematicDimensions(row.width, row.height, row.length),
                allocated_dimensions=SchematicDimensions(
                    row.allocated_width, row.allocated_height, row.allocated_length
                ),
                block_count=row.block_count,
                bounding_volume=row.bounding_volume,
                entity_count=row.entity_count,
                palette_size=row.palette_size,
                region_names=tuple(row.region_names),
                source_data_version=row.source_data_version,
                declared_name=row.declared_name,
                declared_author=row.declared_author,
                signs=_signs_from_json(row.signs),
            ),
            fingerprints=SchematicFingerprints(
                structural=row.fingerprint_structural or "",
                shape=row.fingerprint_shape or "",
                exact=row.fingerprint_exact or "",
                signature_structural=row.signature_structural,
            ),
            analyzer_version=row.analyzer_version,
            analysis_schema_version=row.analysis_schema_version,
            lattice=_lattice_from_json(row.lattice),
        ),
    )


def _lattice_to_json(lattice: AutostackLattice | None) -> dict[str, Any] | None:
    if lattice is None:
        return None
    return {
        "mode": lattice.mode,
        "vectors": [list(vector) for vector in lattice.vectors],
        "coverage": lattice.coverage,
        "cell_min": list(lattice.cell_min),
        "cell_max": list(lattice.cell_max),
        "region_min": list(lattice.region_min),
        "region_max": list(lattice.region_max),
        "label": lattice.label,
    }


def _lattice_from_json(payload: Mapping[str, Any] | None) -> AutostackLattice | None:
    if not payload:
        return None
    return AutostackLattice(
        mode=cast(Literal["1d", "2d"], payload["mode"]),
        vectors=tuple(_vector(vector) for vector in payload["vectors"]),
        coverage=float(payload["coverage"]),
        cell_min=_vector(payload["cell_min"]),
        cell_max=_vector(payload["cell_max"]),
        region_min=_vector(payload["region_min"]),
        region_max=_vector(payload["region_max"]),
        label=payload.get("label"),
    )


def _signs_from_json(payload: Sequence[Mapping[str, Any]]) -> tuple[SchematicSign, ...]:
    return tuple(
        SchematicSign(x=int(sign["x"]), y=int(sign["y"]), z=int(sign["z"]), text=str(sign["text"])) for sign in payload
    )


def _vector(value: Any) -> Vector3:
    parts = cast(Sequence[Any], value)
    return int(parts[0]), int(parts[1]), int(parts[2])
