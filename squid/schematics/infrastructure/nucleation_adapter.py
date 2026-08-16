"""The only module that talks to the native schematic engine.

Everything here is **synchronous and process-local**. It is imported by
:mod:`squid.schematics.infrastructure.worker_main` inside a supervised subprocess, and by
:mod:`squid.schematics.infrastructure.threaded` for development and tests. Importing it
imports the native extension, so nothing in the parent process may import it directly.

Engine handles never leave this module. In particular a :class:`Schematic` is never cached
between calls: :meth:`simulate` attaches a live ``MchprsWorld`` to the object and advances it
tick by tick, so a reused handle would silently carry a half-finished simulation into the next
request. Every operation reloads from bytes instead.

The generated bindings shipped in the wheel differ from the upstream documentation in several
places (`Schematic.create` not `new`, free functions on `Fingerprint`/`Diff`/`Autostack`
rather than methods, base64 strings rather than bytes, `dimensions()` reporting allocated
rather than tight bounds). The translation to domain values happens here so that no caller has
to know any of it.
"""

import base64
import hashlib
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Literal, cast

# The wheel ships a bare `nucleation.abi3.so` with no stub file, so static analysis has
# nothing to read. Every value crossing back out of this module is converted to a domain type
# below, which is where the real checking happens.
import nucleation  # type: ignore[missing-import]

from squid.schematics.application.commands import RenderRequest, SimulationRequest
from squid.schematics.domain.formats import sniff_schematic_format
from squid.schematics.domain.models import (
    AnalyzerCapabilities,
    AutostackLattice,
    FingerprintPreset,
    SchematicAnalysis,
    SchematicComparison,
    SchematicDimensions,
    SchematicFingerprints,
    SchematicFormat,
    SchematicLimits,
    SchematicMetrics,
    SchematicSign,
    SimulationResult,
    Vector3,
    VersionLossEntry,
)
from squid.schematics.errors import InvalidSchematicError, SchematicTooLargeError

logger = logging.getLogger(__name__)

ANALYSIS_SCHEMA_VERSION = 1
"""Bumped whenever the set of facts we derive changes, independently of the engine version."""

_UNKNOWN_DATA_VERSION = -1
"""What the engine reports for a schematic that declares no source data version."""

# Formats the engine can write. Legacy MCEdit `.schematic` output is produced by the
# `"schematic"` writer; there is no writer for vanilla structure `.nbt`.
_EXPORTERS: Mapping[SchematicFormat, str] = {
    SchematicFormat.LITEMATIC: "litematic",
    SchematicFormat.SPONGE_SCHEM: "schematic",
    SchematicFormat.MCSTRUCTURE: "mcstructure",
}

_RESOURCE_PACK_CACHE: dict[str, Any] = {}
"""Engine resource-pack handles cached per persistent worker process by content digest."""


def analyzer_version() -> str:
    """Return the identifier stamped onto every fingerprint this process produces.

    Fingerprints are not comparable across engine versions, so this value is persisted beside
    them and every duplicate lookup filters on it.
    """
    try:
        return f"nucleation-{version('nucleation')}"
    except PackageNotFoundError:  # pragma: no cover - only when running from a source tree
        return "nucleation-unknown"


def capabilities() -> AnalyzerCapabilities:
    """Report what this engine build can do, having already imported it successfully."""
    return AnalyzerCapabilities(
        available=True,
        analyzer_version=analyzer_version(),
        can_render=hasattr(nucleation, "Renderer"),
        can_simulate=hasattr(nucleation, "TickSimulation"),
    )


def analyze(
    data: bytes,
    *,
    limits: SchematicLimits,
    with_lattice: bool = False,
    source_format: SchematicFormat | None = None,
    lattice_max_block_count: int = 200_000,
) -> SchematicAnalysis:
    """Read every fact we persist about one schematic file.

    `source_format` is what the caller's content sniff concluded, including any filename hint
    it had. When omitted the bytes are sniffed again here without a hint.
    """
    schematic = _load(data)
    _guard_allocated_volume(schematic, limits)

    metrics = _metrics(schematic, data, source_format=source_format)
    lattice = None
    if with_lattice and metrics.block_count <= lattice_max_block_count:
        lattice = _detect_lattice(schematic)

    return SchematicAnalysis(
        metrics=metrics,
        fingerprints=_fingerprints(schematic),
        analyzer_version=analyzer_version(),
        analysis_schema_version=ANALYSIS_SCHEMA_VERSION,
        lattice=lattice,
    )


def convert(
    data: bytes, *, target: SchematicFormat, data_version: int | None = None
) -> tuple[bytes, tuple[VersionLossEntry, ...]]:
    """Re-encode a schematic, optionally retargeting it at another Minecraft data version.

    Only the litematic writer reports fidelity losses alongside its output. For the other
    formats a data-version retarget is applied to the loaded schematic first, and that step's
    loss report is returned with the re-encoded bytes.
    """
    schematic = _load(data)

    if data_version is None:
        return _export(schematic, target), ()

    if target is SchematicFormat.LITEMATIC:
        payload = _json_object(schematic.to_litematic_for_version_json(data_version), "conversion result")
        encoded = payload.get("data_b64")
        if not isinstance(encoded, str):
            msg = "The engine returned no converted schematic data."
            raise InvalidSchematicError(msg, developer_action="Check to_litematic_for_version_json's result shape.")
        return base64.b64decode(encoded), _loss_entries(payload.get("loss"))

    source_version = schematic.source_data_version()
    if source_version == _UNKNOWN_DATA_VERSION:
        source_version = schematic.canonical_data_version()
    losses = _loss_entries(_json_value(schematic.convert_to_data_version(data_version, source_version)))
    return _export(schematic, target), losses


def compare(left: bytes, right: bytes, *, preset: FingerprintPreset) -> SchematicComparison:
    """Rank how close two schematics are under one equivalence preset."""
    first, second = _load(left), _load(right)
    diff = nucleation.Diff.compute(first, second, preset.value)
    summary = diff.summary_json()
    return SchematicComparison(
        preset=preset,
        identical=bool(nucleation.Fingerprint.is_duplicate(first, second, preset.value)),
        footprint_distance=float(nucleation.Fingerprint.footprint_distance(first, second, preset.value)),
        # `added`/`removed`/`changed` each hand back a whole `Schematic` of the differing
        # blocks rather than a count, so the scalar the engine already computed is the one to
        # use.
        edit_distance=int(diff.distance()),
        support=float(diff.support()),
        summary=summary if isinstance(summary, str) else None,
    )


def render(data: bytes, *, request: RenderRequest, resource_pack: bytes) -> bytes:
    """Render a schematic to PNG bytes. Phase 3 wires this up; the plumbing exists now."""
    schematic = _load(data)
    config = _render_config(request)
    pack_digest = hashlib.sha256(resource_pack).hexdigest()
    pack = _RESOURCE_PACK_CACHE.get(pack_digest)
    if pack is None:
        pack = nucleation.ResourcePack.from_bytes(resource_pack)
        _RESOURCE_PACK_CACHE[pack_digest] = pack
    return base64.b64decode(nucleation.Renderer.render_png_b64_with_pack(schematic, pack, config))


def _render_config(request: RenderRequest) -> Any:
    """Translate an application render request to Nucleation's camera API."""
    config = nucleation.RenderConfig.create(request.width, request.height)
    config.set_isometric()
    config.set_orthographic(request.projection == "orthographic")
    config.set_sphere_fit(request.sphere_fit)
    if request.yaw is not None:
        config.set_yaw(request.yaw)
    if request.pitch is not None:
        config.set_pitch(request.pitch)
    if request.zoom is not None:
        config.set_zoom(request.zoom)
    config.set_background(*request.background)
    return config


def simulate(data: bytes, *, request: SimulationRequest) -> SimulationResult:
    """Actuate one input and collect moderator-facing piston timing evidence.

    The 0.10.1 tick engine is distinct from the MCHPRS circuit evaluator: it models piston
    movement and vanilla tick ordering and is conformance-tested against captured community
    doors. A saved schematic is loaded in ``InWorld`` mode so placement does not spuriously
    pulse every observer before the moderator presses the input.
    """
    schematic = _load(data)
    try:
        simulation = nucleation.TickSimulation.from_schematic(
            schematic,
            nucleation.TickSettleMode.InWorld,
            0,
            0,
            0,
            "",
        )
    except Exception as exc:
        detail = _optional(lambda: str(nucleation.TickSimulation.last_error_detail()), "")
        msg = detail or str(exc) or "The tick engine could not load this schematic."
        raise InvalidSchematicError(msg, context={"engine_error": msg}) from exc

    if int(simulation.changes_count()) != 0 or not bool(simulation.is_quiescent()):
        msg = "This schematic was already active when loaded, so a timing would be unreliable."
        raise InvalidSchematicError(msg, end_user_action="Save the build while it is completely at rest.")

    snapshot = _json_array(simulation.world_snapshot_json(), "simulation snapshot")
    input_position, input_source = _resolve_simulation_input(schematic, snapshot, request.input_position)
    simulation.use_block(*input_position)
    settled = bool(simulation.run_until_quiescent(request.max_ticks))

    summary = _json_array(simulation.events_summary_json(), "simulation event summary")
    event_rows = [cast(Mapping[str, Any], row) for row in summary if isinstance(row, dict)]
    piston_ticks = [int(row["tick"]) for row in event_rows if int(row.get("piston", 0)) > 0]
    audit = _json_object(nucleation.TickSimulation.block_entity_audit_json(schematic), "block entity audit")
    missing_block_entities = int(audit.get("missing_total", 0))
    unsupported_contacts = int(simulation.piston_retract_contacts())
    notes: list[str] = []
    if not settled:
        notes.append(f"The build did not settle within the {request.max_ticks} gt budget.")
    if missing_block_entities:
        notes.append(f"The file is missing {missing_block_entities} block entity record(s).")
    if unsupported_contacts:
        notes.append(f"The run encountered {unsupported_contacts} unsupported piston/entity contact(s).")

    return SimulationResult(
        ticks_run=int(simulation.tick_count()),
        settled_tick=int(simulation.tick_count()) if settled else None,
        input_position=input_position,
        input_source=input_source,
        last_piston_tick=max(piston_ticks) if piston_ticks else None,
        block_changes=int(simulation.changes_count()),
        piston_events=sum(int(row.get("piston", 0)) for row in event_rows),
        redstone_events=sum(int(row.get("redstone", 0)) for row in event_rows),
        trustworthy=settled and missing_block_entities == 0 and unsupported_contacts == 0 and bool(piston_ticks),
        notes=tuple(notes),
    )


def autostack(data: bytes, *, lattice: AutostackLattice, counts: tuple[int, ...]) -> bytes:
    """Repeat a detected unit cell along its period vectors and re-encode the result."""
    schematic = _load(data)
    if lattice.mode == "1d" and len(lattice.vectors) == 1 and len(counts) == 1:
        x, y, z = lattice.vectors[0]
        resized = nucleation.Autostack.resize_1d(schematic, x, y, z, counts[0])
    elif lattice.mode == "2d" and len(lattice.vectors) == 2 and len(counts) == 2:
        (x1, y1, z1), (x2, y2, z2) = lattice.vectors
        resized = nucleation.Autostack.resize_2d(schematic, x1, y1, z1, x2, y2, z2, counts[0], counts[1])
    else:
        msg = "Autostack repeat counts must match the detected lattice dimensions."
        raise InvalidSchematicError(msg)
    return _export(resized, SchematicFormat.LITEMATIC)


_INPUT_BLOCK_NAMES = ("lever", "_button")


def _resolve_simulation_input(
    schematic: nucleation.Schematic,
    snapshot: Sequence[object],
    manual: Vector3 | None,
) -> tuple[Vector3, Literal["insign", "heuristic", "manual"]]:
    """Choose one actuator without ever guessing between ambiguous controls."""
    controls: dict[Vector3, str] = {}
    for item in snapshot:
        if not isinstance(item, dict):
            continue
        entry = cast(Mapping[str, Any], item)
        state = str(entry.get("state", ""))
        position = _vector(entry.get("pos"))
        if position is not None and any(name in state.partition("[")[0] for name in _INPUT_BLOCK_NAMES):
            controls[position] = state

    compiled = _json_object(schematic.compile_insign_json(), "Insign annotations")
    annotated = _insign_input_positions(compiled)
    annotated_controls = sorted(position for position in annotated if position in controls)
    if len(annotated_controls) == 1:
        return annotated_controls[0], "insign"
    if len(controls) == 1:
        return next(iter(controls)), "heuristic"
    if manual is not None:
        if manual not in controls:
            msg = "The manual input coordinate is not a lever or button in this schematic."
            raise InvalidSchematicError(msg, context={"input_position": manual})
        return manual, "manual"

    if not controls:
        msg = "This schematic has no Insign input annotation and no lever or button."
        action = "Add an @io.* input sign or provide a schematic with an interactable input."
    else:
        msg = "This schematic has several possible inputs, so choosing one automatically would be unsafe."
        action = 'Run the command again with input_position:"x y z".'
    raise InvalidSchematicError(msg, end_user_action=action)


def _insign_input_positions(compiled: Mapping[str, Any]) -> set[Vector3]:
    positions: set[Vector3] = set()
    for name, raw_region in compiled.items():
        if not str(name).startswith("io.") or not isinstance(raw_region, dict):
            continue
        region = cast(Mapping[str, Any], raw_region)
        metadata = region.get("metadata")
        if not isinstance(metadata, dict) or str(metadata.get("type", "")).lower() != "input":
            continue
        for raw_position in cast(Sequence[object], region.get("positions", ())):
            position = _vector(raw_position)
            if position is not None:
                positions.add(position)
        for raw_box in cast(Sequence[object], region.get("bounding_boxes", ())):
            if not isinstance(raw_box, list) or len(raw_box) != 2:
                continue
            low, high = _vector(raw_box[0]), _vector(raw_box[1])
            if low is None or high is None:
                continue
            for x in range(min(low[0], high[0]), max(low[0], high[0]) + 1):
                for y in range(min(low[1], high[1]), max(low[1], high[1]) + 1):
                    for z in range(min(low[2], high[2]), max(low[2], high[2]) + 1):
                        positions.add((x, y, z))
    return positions


def _load(data: bytes) -> nucleation.Schematic:
    """Parse bytes into an engine handle.

    `from_data` is a *constructor*, not an in-place loader: discarding its return value leaves
    you holding an empty 0x0x0 schematic rather than raising, which is why this is the only
    place allowed to call it.
    """
    try:
        return nucleation.Schematic.from_data(data)
    except Exception as exc:
        raise InvalidSchematicError(context={"engine_error": str(exc)}) from exc


def _guard_allocated_volume(schematic: nucleation.Schematic, limits: SchematicLimits) -> None:
    """Refuse a schematic whose allocated bounds are too large to work with.

    Checked against the *allocated* volume rather than the tight one: a file can declare a
    sparsely populated region orders of magnitude bigger than its blocks, and it is the
    allocation that the engine's later passes have to walk.
    """
    dimensions = _dimensions(schematic.dimensions())
    largest_axis = max(dimensions.width, dimensions.height, dimensions.length)
    if largest_axis > limits.max_axis_length:
        raise SchematicTooLargeError(
            actual=largest_axis,
            limit=limits.max_axis_length,
            measure="allocated axis length",
        )
    volume = int(schematic.volume())
    if volume > limits.max_allocated_volume:
        raise SchematicTooLargeError(actual=volume, limit=limits.max_allocated_volume, measure="allocated volume")


def _metrics(
    schematic: nucleation.Schematic, data: bytes, *, source_format: SchematicFormat | None
) -> SchematicMetrics:
    tight = _dimensions(schematic.tight_dimensions())
    resolved_format = source_format or sniff_schematic_format(data) or SchematicFormat.LITEMATIC
    return SchematicMetrics(
        source_format=resolved_format,
        byte_size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        dimensions=tight,
        allocated_dimensions=_dimensions(schematic.dimensions()),
        block_count=int(schematic.block_count()),
        # Materialised rather than derived so a SQL shortlist can range-scan on it. This is
        # the tight bounding box including air, which is *not* the Door Rules cumulative
        # volume: that has hallway, frame, and hitbox exceptions no static read can apply.
        bounding_volume=tight.volume,
        entity_count=_optional(lambda: int(schematic.entity_count()), 0),
        palette_size=_optional(lambda: len(_json_array(schematic.palette_json(), "palette")), 0),
        region_names=_optional(
            lambda: tuple(str(name) for name in _json_array(schematic.region_names_json(), "region names")), ()
        ),
        source_data_version=_optional(lambda: _optional_data_version(schematic), None),
        declared_name=_optional(lambda: _non_empty(schematic.name()), None),
        declared_author=_optional(lambda: _non_empty(schematic.author()), None),
        signs=_optional(lambda: _signs(schematic), ()),
    )


def _optional[T](read: Callable[[], T], missing: T) -> T:
    """Read one piece of optional metadata, treating "this format has no such field" as absent.

    `author()` and `description()` *raise* `NotFound` when the metadata is absent, while
    `name()` and every numeric getter return a fallback, so a display-metadata read is a crash
    risk on some formats and not others. Reported upstream as
    https://github.com/Schem-at/Nucleation/issues/8; the related Sponge reader bug that makes
    `.schem` hit this path is https://github.com/Schem-at/Nucleation/issues/7. Remove this guard
    once both land.

    Every caller here is reading display metadata, so an absent field must not fail the whole
    analysis; the load-bearing measurements above are deliberately left unguarded.
    """
    try:
        return read()
    except Exception:
        logger.debug("Optional schematic metadata is unavailable in this file.", exc_info=True)
        return missing


def _fingerprints(schematic: nucleation.Schematic) -> SchematicFingerprints:
    """Compute all three presets plus the coarse signature used for pre-filtering.

    `structural` is a coarse bucket, not an identity - a build differing by one glass block
    still reports as a structural duplicate - so `shape` is the preset callers should treat as
    "the same build, possibly moved or rotated".
    """
    return SchematicFingerprints(
        structural=str(nucleation.Fingerprint.compute(schematic, FingerprintPreset.STRUCTURAL.value)),
        shape=str(nucleation.Fingerprint.compute(schematic, FingerprintPreset.SHAPE.value)),
        exact=str(nucleation.Fingerprint.compute(schematic, FingerprintPreset.EXACT.value)),
        signature_structural=_non_empty(
            str(nucleation.Fingerprint.signature_json(schematic, FingerprintPreset.STRUCTURAL.value))
        ),
    )


def _detect_lattice(schematic: nucleation.Schematic) -> AutostackLattice | None:
    """Return the repeating unit cell explaining the most of the build, if any.

    Failure here is never fatal: a lattice is opportunistic evidence, and a build with no
    periodicity is the common case rather than an error.
    """
    try:
        candidates = _json_array(nucleation.Autostack.detect_structures(schematic), "autostack result")
    except Exception:
        logger.warning("Repeating-structure detection failed; continuing without a lattice.", exc_info=True)
        return None

    best: AutostackLattice | None = None
    for candidate in candidates:
        lattice = _lattice(candidate)
        if lattice is not None and (best is None or lattice.coverage > best.coverage):
            best = lattice
    return best


def _lattice(candidate: object) -> AutostackLattice | None:
    if not isinstance(candidate, dict):
        return None
    entry = cast(Mapping[str, Any], candidate)
    mode = entry.get("mode")
    vectors = tuple(vector for vector in map(_vector, entry.get("vectors", ())) if vector is not None)
    cell_min, cell_max = _vector(entry.get("cell_min")), _vector(entry.get("cell_max"))
    region_min, region_max = _vector(entry.get("region_min")), _vector(entry.get("region_max"))
    if mode not in ("1d", "2d") or not vectors or None in (cell_min, cell_max, region_min, region_max):
        return None
    return AutostackLattice(
        mode=mode,
        vectors=vectors,
        coverage=float(entry.get("coverage", 0.0)),
        cell_min=cast(Vector3, cell_min),
        cell_max=cast(Vector3, cell_max),
        region_min=cast(Vector3, region_min),
        region_max=cast(Vector3, region_max),
        label=_non_empty(entry.get("label")),
    )


def _signs(schematic: nucleation.Schematic) -> tuple[SchematicSign, ...]:
    """Recover sign text, tolerating an engine payload shape we do not fully pin down.

    Sign text is display-only evidence, so an unexpected entry is dropped rather than failing
    the whole analysis.
    """
    signs: list[SchematicSign] = []
    for entry in _json_array(schematic.extract_signs_json(), "signs"):
        if not isinstance(entry, dict):
            continue
        record = cast(Mapping[str, Any], entry)
        position = _vector(record.get("pos") or record.get("position")) or (
            _coordinate(record.get("x")),
            _coordinate(record.get("y")),
            _coordinate(record.get("z")),
        )
        lines = record.get("lines")
        text = "\n".join(str(line) for line in lines) if isinstance(lines, list) else str(record.get("text", ""))
        if text:
            signs.append(SchematicSign(x=position[0], y=position[1], z=position[2], text=text))
    return tuple(signs)


def _export(schematic: nucleation.Schematic, target: SchematicFormat) -> bytes:
    writer = _EXPORTERS.get(target)
    if writer is None:
        msg = f"The engine cannot write {target.value} files."
        raise InvalidSchematicError(
            msg,
            context={"target": target.value},
            public_context={"target": target.value},
            end_user_action=f"Choose one of: {', '.join(sorted(fmt.value for fmt in _EXPORTERS))}.",
        )
    try:
        return base64.b64decode(schematic.save_as_b64(writer, "", "{}"))
    except Exception as exc:
        raise InvalidSchematicError(
            context={"target": target.value, "engine_error": str(exc)},
            public_context={"target": target.value},
        ) from exc


def _loss_entries(payload: object) -> tuple[VersionLossEntry, ...]:
    """Translate the engine's fidelity-loss report, skipping entries we cannot read."""
    if not isinstance(payload, list):
        return ()
    entries: list[VersionLossEntry] = []
    for item in cast(list[object], payload):
        if not isinstance(item, dict):
            continue
        record = cast(Mapping[str, Any], item)
        severity = str(record.get("severity", "Loss"))
        entries.append(
            VersionLossEntry(
                version=str(record.get("version", "")),
                kind=str(record.get("kind", "unknown")),
                severity="Approximated" if severity == "Approximated" else "Loss",
                path=str(record.get("path", "")),
                detail=str(record.get("detail", record.get("message", ""))),
            )
        )
    return tuple(entries)


def _dimensions(reported: object) -> SchematicDimensions:
    return SchematicDimensions(
        width=int(getattr(reported, "x", 0)),
        height=int(getattr(reported, "y", 0)),
        length=int(getattr(reported, "z", 0)),
    )


def _optional_data_version(schematic: nucleation.Schematic) -> int | None:
    declared = int(schematic.source_data_version())
    return None if declared == _UNKNOWN_DATA_VERSION else declared


def _vector(value: object) -> Vector3 | None:
    if isinstance(value, Sequence) and not isinstance(value, str | bytes) and len(value) == 3:
        parts = cast(Sequence[object], value)
        try:
            return int(parts[0]), int(parts[1]), int(parts[2])  # type: ignore[arg-type]
        except TypeError, ValueError:
            return None
    return None


def _coordinate(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except TypeError, ValueError:
        return 0


def _non_empty(value: object) -> str | None:
    text = str(value) if value is not None else ""
    return text or None


def _json_value(payload: str) -> object:
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise InvalidSchematicError(
            context={"reason": "engine returned malformed JSON"},
            developer_action="The engine's *_json accessors are expected to return valid JSON.",
        ) from exc


def _json_array(payload: str, what: str) -> list[object]:
    value = _json_value(payload)
    if not isinstance(value, list):
        msg = f"The engine returned an unexpected {what} payload."
        raise InvalidSchematicError(msg, context={"payload_type": type(value).__name__})
    return cast(list[object], value)


def _json_object(payload: str, what: str) -> Mapping[str, Any]:
    value = _json_value(payload)
    if not isinstance(value, dict):
        msg = f"The engine returned an unexpected {what} payload."
        raise InvalidSchematicError(msg, context={"payload_type": type(value).__name__})
    return cast(Mapping[str, Any], value)
