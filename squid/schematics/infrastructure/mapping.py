"""Translation between schematic domain values and their persisted rows."""

from collections.abc import Mapping, Sequence
from math import isfinite
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
            sanitization_report=_json_object(row.sanitization_report, field="sanitization_report")
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
                region_names=tuple(
                    _string(name, field="region_names item")
                    for name in _sequence(row.region_names, field="region_names")
                ),
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


def _lattice_from_json(payload: object | None) -> AutostackLattice | None:
    if payload is None:
        return None
    value = _mapping(payload, field="lattice")
    mode = cast(Literal["1d", "2d"], _literal(value["mode"], ("1d", "2d"), field="lattice.mode"))
    vectors = tuple(
        _vector(vector, field="lattice.vectors item") for vector in _sequence(value["vectors"], field="lattice.vectors")
    )
    expected_vectors = 1 if mode == "1d" else 2
    if len(vectors) != expected_vectors:
        msg = f"lattice.vectors must contain {expected_vectors} vector(s) for {mode} mode"
        raise ValueError(msg)
    coverage = _number(value["coverage"], field="lattice.coverage")
    if not 0.0 <= coverage <= 1.0:
        msg = "lattice.coverage must be between zero and one"
        raise ValueError(msg)
    label = value.get("label")
    return AutostackLattice(
        mode=mode,
        vectors=vectors,
        coverage=coverage,
        cell_min=_vector(value["cell_min"], field="lattice.cell_min"),
        cell_max=_vector(value["cell_max"], field="lattice.cell_max"),
        region_min=_vector(value["region_min"], field="lattice.region_min"),
        region_max=_vector(value["region_max"], field="lattice.region_max"),
        label=None if label is None else _string(label, field="lattice.label"),
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


def _simulation_from_json(payload: object | None) -> SimulationResult | None:
    if payload is None:
        return None
    value = _mapping(payload, field="simulation_evidence")
    input_position = value.get("input_position")
    source = value.get("input_source")
    settled_tick = value.get("settled_tick")
    last_piston_tick = value.get("last_piston_tick")
    return SimulationResult(
        ticks_run=_integer(value["ticks_run"], field="simulation_evidence.ticks_run"),
        settled_tick=(
            None if settled_tick is None else _integer(settled_tick, field="simulation_evidence.settled_tick")
        ),
        input_position=(
            None if input_position is None else _vector(input_position, field="simulation_evidence.input_position")
        ),
        input_source=(
            None
            if source is None
            else _literal(source, ("insign", "heuristic", "manual"), field="simulation_evidence.input_source")
        ),
        last_piston_tick=(
            None
            if last_piston_tick is None
            else _integer(last_piston_tick, field="simulation_evidence.last_piston_tick")
        ),
        block_changes=_integer(value.get("block_changes", 0), field="simulation_evidence.block_changes"),
        piston_events=_integer(value.get("piston_events", 0), field="simulation_evidence.piston_events"),
        redstone_events=_integer(value.get("redstone_events", 0), field="simulation_evidence.redstone_events"),
        trustworthy=_boolean(value.get("trustworthy", False), field="simulation_evidence.trustworthy"),
        samples=tuple(
            _simulation_sample(sample)
            for sample in _sequence(value.get("samples", ()), field="simulation_evidence.samples")
        ),
        notes=tuple(
            _string(note, field="simulation_evidence.notes item")
            for note in _sequence(value.get("notes", ()), field="simulation_evidence.notes")
        ),
    )


def _simulation_sample(payload: object) -> SimulationSample:
    sample = _mapping(payload, field="simulation_evidence.samples item")
    return SimulationSample(
        tick=_integer(sample["tick"], field="simulation sample tick"),
        x=_integer(sample["x"], field="simulation sample x"),
        y=_integer(sample["y"], field="simulation sample y"),
        z=_integer(sample["z"], field="simulation sample z"),
        powered=_boolean(sample["powered"], field="simulation sample powered"),
        signal_strength=_integer(sample["signal_strength"], field="simulation sample signal_strength"),
    )


def _signs_from_json(payload: object) -> tuple[SchematicSign, ...]:
    return tuple(
        SchematicSign(
            x=_integer(sign["x"], field="sign.x"),
            y=_integer(sign["y"], field="sign.y"),
            z=_integer(sign["z"], field="sign.z"),
            text=_string(sign["text"], field="sign.text"),
        )
        for sign in (_mapping(item, field="signs item") for item in _sequence(payload, field="signs"))
    )


def _vector(value: object, *, field: str) -> Vector3:
    parts = _sequence(value, field=field)
    if len(parts) != 3:
        msg = f"{field} must contain exactly three coordinates"
        raise ValueError(msg)
    return (
        _integer(parts[0], field=f"{field}[0]"),
        _integer(parts[1], field=f"{field}[1]"),
        _integer(parts[2], field=f"{field}[2]"),
    )


def _mapping(value: object, *, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        msg = f"{field} must be a JSON object with string keys"
        raise TypeError(msg)
    return value


def _sequence(value: object, *, field: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        msg = f"{field} must be a JSON array"
        raise TypeError(msg)
    return value


def _integer(value: object, *, field: str) -> int:
    if type(value) is not int:
        msg = f"{field} must be an integer"
        raise TypeError(msg)
    return value


def _number(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not isfinite(value):
        msg = f"{field} must be a finite number"
        raise TypeError(msg)
    return float(value)


def _boolean(value: object, *, field: str) -> bool:
    if type(value) is not bool:
        msg = f"{field} must be a boolean"
        raise TypeError(msg)
    return value


def _string(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        msg = f"{field} must be a string"
        raise TypeError(msg)
    return value


def _literal[T: str](value: object, options: tuple[T, ...], *, field: str) -> T:
    if value not in options:
        msg = f"{field} must be one of {options}"
        raise ValueError(msg)
    return cast(T, value)


def _json_object(value: object, *, field: str) -> dict[str, JSONValue]:
    mapping = _mapping(value, field=field)
    return {key: _json_value(item, field=f"{field}.{key}") for key, item in mapping.items()}


def _json_value(value: object, *, field: str) -> JSONValue:
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if isfinite(value):
            return value
        msg = f"{field} must be finite"
        raise ValueError(msg)
    if isinstance(value, Mapping):
        return _json_object(value, field=field)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_json_value(item, field=field) for item in value]
    msg = f"{field} is not a JSON value"
    raise TypeError(msg)
