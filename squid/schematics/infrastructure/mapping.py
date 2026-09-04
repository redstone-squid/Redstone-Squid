"""Translation between schematic domain values and their persisted rows."""

from collections.abc import Mapping, Sequence
from typing import Any, Literal, cast

from squid.core.errors import DataIntegrityError, JSONValue
from squid.schematics.application.attachments import SchematicPublication, StoredSchematic
from squid.schematics.domain.models import (
    AutostackLattice,
    SchematicAnalysis,
    SchematicDimensions,
    SchematicFingerprints,
    SchematicFormat,
    SchematicLicense,
    SchematicMetrics,
    SchematicSign,
    SchematicVisibility,
    SimulationResult,
    SimulationSample,
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


def to_stored_schematic(row: BuildSchematic, *, source_format: str, byte_size: int) -> StoredSchematic:
    """Rebuild the read model, taking the two file-level facts from `schematic_files`.

    `source_format` and `byte_size` describe the bytes, not the analysis, so they live on the
    content-addressed file row and are joined in rather than duplicated per attachment.
    """
    try:
        return _to_stored_schematic(row, source_format=source_format, byte_size=byte_size)
    except DataIntegrityError as error:
        if error.context.get("schematic_id") == row.id:
            raise
        raise _mapping_error(row.id) from error
    except (AttributeError, IndexError, KeyError, OverflowError, TypeError, ValueError) as error:
        raise _mapping_error(row.id) from error


def _to_stored_schematic(row: BuildSchematic, *, source_format: str, byte_size: int) -> StoredSchematic:
    return StoredSchematic(
        id=row.id,
        build_id=row.build_id,
        file_sha256=row.file_sha256,
        is_primary=row.is_primary,
        original_filename=row.original_filename,
        publication=SchematicPublication(
            visibility=SchematicVisibility(row.visibility),
            license=SchematicLicense(row.license_code) if row.license_code is not None else None,
            rights_attested_at=row.rights_attested_at,
            rights_attested_by_account_id=row.rights_attested_by_account_id,
            sanitized_at=row.sanitized_at,
            sanitizer_version=row.sanitizer_version,
            sanitization_report=cast(dict[str, JSONValue], row.sanitization_report)
            if row.sanitization_report is not None
            else None,
            published_at=row.published_at,
            withdrawn_at=row.withdrawn_at,
        ),
        analysis=SchematicAnalysis(
            metrics=SchematicMetrics(
                source_format=SchematicFormat(source_format),
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
        simulation_evidence=_simulation_from_json(row.simulation_evidence),
    )


def _mapping_error(schematic_id: int) -> DataIntegrityError:
    msg = f"Persisted schematic {schematic_id} contains an invalid enum or JSON value."
    return DataIntegrityError(msg, context={"schematic_id": schematic_id})


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


def simulation_to_json(result: SimulationResult) -> dict[str, Any]:
    """Flatten persisted simulation evidence into JSON-safe values."""
    return {
        "ticks_run": result.ticks_run,
        "settled_tick": result.settled_tick,
        "input_position": list(result.input_position) if result.input_position is not None else None,
        "input_source": result.input_source,
        "last_piston_tick": result.last_piston_tick,
        "block_changes": result.block_changes,
        "piston_events": result.piston_events,
        "redstone_events": result.redstone_events,
        "trustworthy": result.trustworthy,
        "samples": [
            {
                "tick": sample.tick,
                "x": sample.x,
                "y": sample.y,
                "z": sample.z,
                "powered": sample.powered,
                "signal_strength": sample.signal_strength,
            }
            for sample in result.samples
        ],
        "notes": list(result.notes),
    }


def _simulation_from_json(payload: Mapping[str, Any] | None) -> SimulationResult | None:
    if not payload:
        return None
    input_position = payload.get("input_position")
    source = payload.get("input_source")
    return SimulationResult(
        ticks_run=int(payload["ticks_run"]),
        settled_tick=int(payload["settled_tick"]) if payload.get("settled_tick") is not None else None,
        input_position=_vector(input_position) if input_position is not None else None,
        input_source=cast(Literal["insign", "heuristic", "manual"], source) if source else None,
        last_piston_tick=(int(payload["last_piston_tick"]) if payload.get("last_piston_tick") is not None else None),
        block_changes=int(payload.get("block_changes", 0)),
        piston_events=int(payload.get("piston_events", 0)),
        redstone_events=int(payload.get("redstone_events", 0)),
        trustworthy=bool(payload.get("trustworthy", False)),
        samples=tuple(
            SimulationSample(
                tick=int(sample["tick"]),
                x=int(sample["x"]),
                y=int(sample["y"]),
                z=int(sample["z"]),
                powered=bool(sample["powered"]),
                signal_strength=int(sample["signal_strength"]),
            )
            for sample in cast(Sequence[Mapping[str, Any]], payload.get("samples", ()))
        ),
        notes=tuple(str(note) for note in cast(Sequence[object], payload.get("notes", ()))),
    )


def _signs_from_json(payload: Sequence[Mapping[str, Any]]) -> tuple[SchematicSign, ...]:
    return tuple(
        SchematicSign(x=int(sign["x"]), y=int(sign["y"]), z=int(sign["z"]), text=str(sign["text"])) for sign in payload
    )


def _vector(value: Any) -> Vector3:
    parts = cast(Sequence[Any], value)
    return int(parts[0]), int(parts[1]), int(parts[2])
