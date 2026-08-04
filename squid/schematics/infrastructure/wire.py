"""The frame format and value encoding shared by the worker supervisor and its children.

Standard library only, and deliberately free of any engine import: the parent process links
against this module, and the whole point of the worker design is that the parent never loads
the native extension.

A frame is::

    | 4 bytes BE header length | 4 bytes BE body length | header JSON (utf-8) | body |

Binary payloads travel in the body, appended raw. Schematic files, converted output, and
rendered PNGs are megabyte-scale; base64-ing them into the JSON header would inflate every
transfer by a third for no benefit. The header's `parts` field carries the length of each
payload so a multi-payload request such as `compare` can be split back apart.
"""

import asyncio
import dataclasses
import json
from collections.abc import Mapping, Sequence
from typing import Any, Literal, cast

from squid.schematics.domain.models import (
    AnalyzerCapabilities,
    AutostackLattice,
    FingerprintPreset,
    SchematicAnalysis,
    SchematicComparison,
    SchematicDimensions,
    SchematicFingerprints,
    SchematicFormat,
    SchematicMetrics,
    SchematicSign,
    SimulationResult,
    SimulationSample,
    Vector3,
    VersionLossEntry,
)

LENGTH_PREFIX_BYTES = 4
MAX_FRAME_BYTES = 256 * 1024 * 1024
"""Sanity bound on a single frame, so a desynchronised stream cannot ask us to allocate a
nonsensical buffer before we notice."""

type Operation = Literal["capabilities", "analyze", "convert", "compare", "render", "simulate", "autostack"]
type ErrorKind = Literal["invalid", "too_large", "unavailable", "internal"]


@dataclasses.dataclass(frozen=True, slots=True)
class Frame:
    """One request or response: a JSON header plus zero or more binary payloads."""

    header: Mapping[str, Any]
    payloads: tuple[bytes, ...] = ()

    def encode(self) -> bytes:
        header = {**self.header, "parts": [len(payload) for payload in self.payloads]}
        header_bytes = json.dumps(header, separators=(",", ":")).encode("utf-8")
        body = b"".join(self.payloads)
        return (
            len(header_bytes).to_bytes(LENGTH_PREFIX_BYTES, "big")
            + len(body).to_bytes(LENGTH_PREFIX_BYTES, "big")
            + header_bytes
            + body
        )


class FrameStreamClosed(Exception):
    """The peer closed the stream, cleanly or otherwise."""


async def read_frame(stream: asyncio.StreamReader) -> Frame:
    """Read exactly one frame, raising :class:`FrameStreamClosed` at end of stream."""
    try:
        header_length = int.from_bytes(await stream.readexactly(LENGTH_PREFIX_BYTES), "big")
        body_length = int.from_bytes(await stream.readexactly(LENGTH_PREFIX_BYTES), "big")
        if header_length + body_length > MAX_FRAME_BYTES:
            msg = "Frame exceeds the maximum size; the stream is out of sync."
            raise FrameStreamClosed(msg)
        header = cast(Mapping[str, Any], json.loads((await stream.readexactly(header_length)).decode("utf-8")))
        body = await stream.readexactly(body_length)
    except (asyncio.IncompleteReadError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise FrameStreamClosed(str(exc)) from exc

    payloads: list[bytes] = []
    offset = 0
    for size in header.get("parts", [body_length] if body_length else []):
        payloads.append(body[offset : offset + int(size)])
        offset += int(size)
    return Frame(header, tuple(payloads))


def encode_analysis(analysis: SchematicAnalysis) -> Mapping[str, Any]:
    """Flatten an analysis into JSON-safe primitives.

    `dataclasses.asdict` is enough on the way out because every field is a primitive, a
    `StrEnum` (already a `str`), or a nested frozen dataclass. Decoding is written out by hand
    because rebuilding typed nested dataclasses is not something `asdict` can invert.
    """
    return cast(Mapping[str, Any], dataclasses.asdict(analysis))


def decode_analysis(payload: Mapping[str, Any]) -> SchematicAnalysis:
    lattice = payload.get("lattice")
    return SchematicAnalysis(
        metrics=_decode_metrics(_mapping(payload["metrics"])),
        fingerprints=_decode_fingerprints(_mapping(payload["fingerprints"])),
        analyzer_version=str(payload["analyzer_version"]),
        analysis_schema_version=int(payload["analysis_schema_version"]),
        lattice=decode_lattice(_mapping(lattice)) if lattice is not None else None,
    )


def encode_lattice(lattice: AutostackLattice) -> Mapping[str, Any]:
    return cast(Mapping[str, Any], dataclasses.asdict(lattice))


def decode_lattice(payload: Mapping[str, Any]) -> AutostackLattice:
    label = payload.get("label")
    return AutostackLattice(
        mode=cast(Literal["1d", "2d"], payload["mode"]),
        vectors=tuple(_vector(vector) for vector in payload["vectors"]),
        coverage=float(payload["coverage"]),
        cell_min=_vector(payload["cell_min"]),
        cell_max=_vector(payload["cell_max"]),
        region_min=_vector(payload["region_min"]),
        region_max=_vector(payload["region_max"]),
        label=str(label) if label is not None else None,
    )


def encode_comparison(comparison: SchematicComparison) -> Mapping[str, Any]:
    return cast(Mapping[str, Any], dataclasses.asdict(comparison))


def decode_comparison(payload: Mapping[str, Any]) -> SchematicComparison:
    return SchematicComparison(
        preset=FingerprintPreset(payload["preset"]),
        identical=bool(payload["identical"]),
        footprint_distance=float(payload["footprint_distance"]),
        edit_distance=_optional_int(payload.get("edit_distance")),
        support=_optional_float(payload.get("support")),
        summary=_optional_str(payload.get("summary")),
    )


def encode_losses(losses: Sequence[VersionLossEntry]) -> list[Mapping[str, Any]]:
    return [cast(Mapping[str, Any], dataclasses.asdict(loss)) for loss in losses]


def decode_losses(payload: object) -> tuple[VersionLossEntry, ...]:
    if not isinstance(payload, list):
        return ()
    return tuple(
        VersionLossEntry(
            version=str(entry["version"]),
            kind=str(entry["kind"]),
            severity=cast(Literal["Loss", "Approximated"], entry["severity"]),
            path=str(entry["path"]),
            detail=str(entry["detail"]),
        )
        for entry in cast(list[Mapping[str, Any]], payload)
    )


def encode_capabilities(capabilities: AnalyzerCapabilities) -> Mapping[str, Any]:
    return cast(Mapping[str, Any], dataclasses.asdict(capabilities))


def decode_capabilities(payload: Mapping[str, Any]) -> AnalyzerCapabilities:
    return AnalyzerCapabilities(
        available=bool(payload["available"]),
        analyzer_version=_optional_str(payload.get("analyzer_version")),
        can_render=bool(payload.get("can_render", False)),
        can_simulate=bool(payload.get("can_simulate", False)),
        render_backends=tuple(str(backend) for backend in payload.get("render_backends", ())),
        unavailable_reason=_optional_str(payload.get("unavailable_reason")),
    )


def encode_simulation(result: SimulationResult) -> Mapping[str, Any]:
    return cast(Mapping[str, Any], dataclasses.asdict(result))


def decode_simulation(payload: Mapping[str, Any]) -> SimulationResult:
    input_position = payload.get("input_position")
    input_source = payload.get("input_source")
    return SimulationResult(
        ticks_run=int(payload["ticks_run"]),
        settled_tick=_optional_int(payload.get("settled_tick")),
        input_position=_vector(input_position) if input_position is not None else None,
        input_source=cast(Literal["insign", "heuristic", "manual"], input_source) if input_source else None,
        last_piston_tick=_optional_int(payload.get("last_piston_tick")),
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
            for sample in cast(list[Mapping[str, Any]], payload.get("samples", []))
        ),
        notes=tuple(str(note) for note in payload.get("notes", ())),
    )


def _decode_metrics(payload: Mapping[str, Any]) -> SchematicMetrics:
    return SchematicMetrics(
        source_format=SchematicFormat(payload["source_format"]),
        byte_size=int(payload["byte_size"]),
        sha256=str(payload["sha256"]),
        dimensions=_decode_dimensions(_mapping(payload["dimensions"])),
        allocated_dimensions=_decode_dimensions(_mapping(payload["allocated_dimensions"])),
        block_count=int(payload["block_count"]),
        bounding_volume=int(payload["bounding_volume"]),
        entity_count=int(payload["entity_count"]),
        palette_size=int(payload["palette_size"]),
        region_names=tuple(str(name) for name in payload.get("region_names", ())),
        source_data_version=_optional_int(payload.get("source_data_version")),
        declared_name=_optional_str(payload.get("declared_name")),
        declared_author=_optional_str(payload.get("declared_author")),
        signs=tuple(
            SchematicSign(x=int(sign["x"]), y=int(sign["y"]), z=int(sign["z"]), text=str(sign["text"]))
            for sign in cast(list[Mapping[str, Any]], payload.get("signs", []))
        ),
    )


def _decode_fingerprints(payload: Mapping[str, Any]) -> SchematicFingerprints:
    return SchematicFingerprints(
        structural=str(payload["structural"]),
        shape=str(payload["shape"]),
        exact=str(payload["exact"]),
        signature_structural=_optional_str(payload.get("signature_structural")),
    )


def _decode_dimensions(payload: Mapping[str, Any]) -> SchematicDimensions:
    return SchematicDimensions(
        width=int(payload["width"]), height=int(payload["height"]), length=int(payload["length"])
    )


def _mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        msg = f"Expected a JSON object, got {type(value).__name__}."
        raise TypeError(msg)
    return cast(Mapping[str, Any], value)


def _vector(value: object) -> Vector3:
    parts = cast(Sequence[Any], value)
    return int(parts[0]), int(parts[1]), int(parts[2])


def _optional_int(value: object) -> int | None:
    return None if value is None else int(cast(int, value))


def _optional_float(value: object) -> float | None:
    return None if value is None else float(cast(float, value))


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)
